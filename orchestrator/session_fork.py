"""Helpers for forking (diverging) a CC session JSONL at a message boundary.

A diverged conversation is created by copying the source session JSONL up to
a chosen entry (located by its `uuid`), rewriting the session id on every
line, and writing the result as a fresh `<new_sid>.jsonl` that
`claude --resume <new_sid>` can pick up.

The forked transcript ends with a standard CC interrupt marker
("[Request interrupted by user]") — a fork IS an interruption: the
conversation was cut at that point and waits for the user's new direction.
The marker doubles as the idle signal for xylocopa's status derivation
(sync parses it as kind="interrupt" → agent stays IDLE), so the state
machine converges from JSONL truth alone with no out-of-band status write.

Pure line-list functions — no filesystem access — so they are unit-testable.
"""

import json
import uuid as _uuid
from datetime import datetime, timezone

INTERRUPT_TEXT = "[Request interrupted by user]"

# Entry fields inherited from the last kept entry so the marker matches the
# surrounding transcript (CC writes these on every user entry).
_INHERIT_FIELDS = ("cwd", "gitBranch", "version", "userType", "entrypoint")

# Entry types that carry conversation turns. Everything else (attachment,
# mode, permission-mode, ai-title, last-prompt, file-history-snapshot,
# queue-operation, summary, ...) is positional metadata.
_CONVERSATIONAL_TYPES = {"user", "assistant", "system"}


def _parse(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        entry = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return entry if isinstance(entry, dict) else None


def find_uuid_index(lines: list[str], target_uuid: str) -> int:
    """Index of the line whose entry `uuid` equals target_uuid, or -1."""
    for i, line in enumerate(lines):
        entry = _parse(line)
        if entry and entry.get("uuid") == target_uuid:
            return i
    return -1


def _ends_with_dangling_tool_use(entry: dict) -> bool:
    """True for assistant entries whose last content block is a tool_use.

    Truncating right after such a line leaves an unanswered tool call in the
    transcript — the resumed CLI would send an assistant turn ending in
    tool_use with no tool_result, which the API rejects.
    """
    if entry.get("type") != "assistant":
        return False
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list) or not content:
        return False
    last = content[-1]
    return isinstance(last, dict) and last.get("type") == "tool_use"


def trim_dangling_tail(lines: list[str]) -> list[str]:
    """Trim the tail so the transcript ends on a resumable entry.

    Walks back to the last conversational entry that does not end with an
    unanswered tool_use and cuts there. Trailing metadata lines (mode,
    ai-title, ...) past that point are dropped with it — they only carry UI
    state and are safe to lose.
    """
    for j in range(len(lines) - 1, -1, -1):
        entry = _parse(lines[j])
        if entry is None:
            continue
        if entry.get("type") not in _CONVERSATIONAL_TYPES:
            continue
        if _ends_with_dangling_tool_use(entry):
            continue
        return lines[: j + 1]
    return []


def rewrite_session_id(lines: list[str], new_session_id: str) -> list[str]:
    """Rewrite sessionId/session_id on every parseable line.

    Unparseable lines are kept verbatim; entries without a session key
    (e.g. file-history-snapshot) pass through untouched.
    """
    out = []
    for line in lines:
        entry = _parse(line)
        if entry is None:
            out.append(line)
            continue
        changed = False
        if "sessionId" in entry:
            entry["sessionId"] = new_session_id
            changed = True
        if "session_id" in entry:
            entry["session_id"] = new_session_id
            changed = True
        if changed:
            out.append(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
        else:
            out.append(line)
    return out


def make_interrupt_line(kept_lines: list[str], new_session_id: str) -> str:
    """Build a CC-format interrupt marker entry closing the forked transcript.

    Mirrors the exact shape CC writes on ESC (user entry, content list with
    a single text block). parentUuid chains to the last kept entry that has
    a uuid; cwd/gitBranch/version etc. are inherited from the tail so the
    marker matches the surrounding transcript.
    """
    parent_uuid = None
    inherited: dict = {}
    for line in reversed(kept_lines):
        entry = _parse(line)
        if entry is None:
            continue
        if parent_uuid is None and entry.get("uuid"):
            parent_uuid = entry["uuid"]
        for f in _INHERIT_FIELDS:
            if f not in inherited and f in entry:
                inherited[f] = entry[f]
        if parent_uuid and len(inherited) == len(_INHERIT_FIELDS):
            break
    marker = {
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "promptId": str(_uuid.uuid4()),
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": INTERRUPT_TEXT}],
        },
        "uuid": str(_uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "sessionId": new_session_id,
        **inherited,
    }
    return json.dumps(marker, ensure_ascii=False, separators=(",", ":"))


def fork_session_lines(
    lines: list[str],
    target_uuid: str,
    include_target: bool,
    new_session_id: str,
) -> list[str] | None:
    """Build the forked transcript: truncate at target, trim, rewrite sid,
    close with an interrupt marker.

    include_target=True keeps the target entry (diverge at an AGENT message:
    the reply stays, the new direction starts after it). False cuts just
    before it (diverge at a USER message: edit-and-resend semantics).

    Returns None when target_uuid is not present, or when nothing resumable
    remains after truncation (e.g. forking before the very first prompt).
    """
    idx = find_uuid_index(lines, target_uuid)
    if idx < 0:
        return None
    kept = lines[: idx + 1] if include_target else lines[:idx]
    kept = trim_dangling_tail(kept)
    if not any((_parse(ln) or {}).get("type") in _CONVERSATIONAL_TYPES for ln in kept):
        return None
    out = rewrite_session_id(kept, new_session_id)
    out.append(make_interrupt_line(out, new_session_id))
    return out
