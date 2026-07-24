"""Tests for session_fork — pure line-list fork helpers.

Synthetic JSONL lines only. No filesystem access.
"""
from __future__ import annotations

import json

from session_fork import (
    INTERRUPT_TEXT,
    find_uuid_index,
    fork_session_lines,
    make_interrupt_line,
    rewrite_session_id,
    trim_dangling_tail,
)

SID = "src-session-1"


def _line(entry):
    return json.dumps(entry)


def _user(uuid, text, sid=SID):
    return _line({
        "type": "user", "uuid": uuid, "parentUuid": None, "sessionId": sid,
        "message": {"role": "user", "content": text},
    })


def _assistant(uuid, blocks, sid=SID):
    return _line({
        "type": "assistant", "uuid": uuid, "parentUuid": None,
        "sessionId": sid, "session_id": sid,
        "message": {"role": "assistant", "content": blocks},
    })


def _text_block(text):
    return {"type": "text", "text": text}


def _tool_use_block(tid="toolu_01"):
    return {"type": "tool_use", "id": tid, "name": "Bash", "input": {}}


def _meta(kind="mode", sid=SID):
    return _line({"type": kind, "sessionId": sid})


def _conversation():
    """u1 → a1(text) → a2(tool_use) → u-tr(tool_result) → a3(text) → u2 → a4(text)."""
    return [
        _user("u1", "first question"),
        _assistant("a1", [_text_block("thinking about it")]),
        _assistant("a2", [_tool_use_block()]),
        _line({
            "type": "user", "uuid": "u-tr", "sessionId": SID,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_01", "content": "ok"},
            ]},
        }),
        _assistant("a3", [_text_block("the answer")]),
        _user("u2", "follow-up question"),
        _assistant("a4", [_text_block("follow-up answer")]),
    ]


def _types(lines):
    return [json.loads(ln)["type"] for ln in lines]


def _uuids(lines):
    return [json.loads(ln).get("uuid") for ln in lines]


# --- find_uuid_index ---

def test_find_uuid_index_hit_and_miss():
    lines = _conversation()
    assert find_uuid_index(lines, "a3") == 4
    assert find_uuid_index(lines, "nope") == -1


def test_find_uuid_index_skips_blank_and_garbage():
    lines = ["", "not json {{{", _user("u1", "hi")]
    assert find_uuid_index(lines, "u1") == 2


# --- trim_dangling_tail ---

def test_trim_drops_trailing_tool_use_line():
    lines = [_user("u1", "q"), _assistant("a1", [_text_block("t")]),
             _assistant("a2", [_tool_use_block()])]
    assert _uuids(trim_dangling_tail(lines)) == ["u1", "a1"]


def test_trim_drops_mixed_block_line_ending_in_tool_use():
    lines = [_user("u1", "q"),
             _assistant("a1", [_text_block("t"), _tool_use_block()])]
    assert _uuids(trim_dangling_tail(lines)) == ["u1"]


def test_trim_keeps_tool_result_tail():
    lines = _conversation()[:4]  # ends with u-tr tool_result
    assert trim_dangling_tail(lines) == lines


def test_trim_drops_trailing_metadata_with_dangling_tool_use():
    lines = [_user("u1", "q"), _assistant("a2", [_tool_use_block()]), _meta()]
    assert _uuids(trim_dangling_tail(lines)) == ["u1"]


def test_trim_no_op_on_clean_tail():
    lines = _conversation()
    assert trim_dangling_tail(lines) == lines


# --- rewrite_session_id ---

def test_rewrite_both_session_keys():
    out = rewrite_session_id(_conversation(), "new-sid")
    for ln in out:
        entry = json.loads(ln)
        if "sessionId" in entry:
            assert entry["sessionId"] == "new-sid"
        if "session_id" in entry:
            assert entry["session_id"] == "new-sid"


def test_rewrite_passes_through_unparseable_and_keyless():
    snapshot = _line({"type": "file-history-snapshot", "messageId": "m1"})
    out = rewrite_session_id(["garbage {{{", snapshot], "new-sid")
    assert out[0] == "garbage {{{"
    assert json.loads(out[1]) == {"type": "file-history-snapshot", "messageId": "m1"}


# --- make_interrupt_line ---

def test_interrupt_line_shape_and_chain():
    kept = rewrite_session_id(_conversation(), "new-sid")
    marker = json.loads(make_interrupt_line(kept, "new-sid"))
    assert marker["type"] == "user"
    assert marker["message"]["content"] == [{"type": "text", "text": INTERRUPT_TEXT}]
    assert marker["parentUuid"] == "a4"  # last kept entry with a uuid
    assert marker["sessionId"] == "new-sid"
    assert marker["uuid"] and marker["uuid"] != "a4"
    assert marker["isSidechain"] is False


# --- fork_session_lines ---

def _split_marker(out):
    """Return (kept_uuids, marker_entry) from fork output."""
    marker = json.loads(out[-1])
    return _uuids(out[:-1]), marker


def test_fork_at_agent_message_inclusive():
    out = fork_session_lines(_conversation(), "a3", True, "new-sid")
    kept, marker = _split_marker(out)
    assert kept == ["u1", "a1", "a2", "u-tr", "a3"]
    assert all(json.loads(ln)["sessionId"] == "new-sid" for ln in out)
    # transcript closes with the interrupt marker chained to the fork point
    assert marker["message"]["content"][0]["text"] == INTERRUPT_TEXT
    assert marker["parentUuid"] == "a3"


def test_fork_before_user_message_exclusive():
    out = fork_session_lines(_conversation(), "u2", False, "new-sid")
    # cut before u2 → prefix ends at a3 (clean text tail, no trim needed)
    kept, marker = _split_marker(out)
    assert kept == ["u1", "a1", "a2", "u-tr", "a3"]
    assert marker["parentUuid"] == "a3"


def test_fork_before_user_message_trims_dangling_tool_use():
    lines = [_user("u1", "q"), _assistant("a2", [_tool_use_block()]),
             _user("u2", "interrupting prompt")]
    out = fork_session_lines(lines, "u2", False, "new-sid")
    kept, marker = _split_marker(out)
    assert kept == ["u1"]
    assert marker["parentUuid"] == "u1"


def test_fork_unknown_uuid_returns_none():
    assert fork_session_lines(_conversation(), "missing", True, "n") is None


def test_fork_before_first_prompt_returns_none():
    lines = [_meta(), _user("u1", "first")]
    assert fork_session_lines(lines, "u1", False, "n") is None


def test_fork_keeps_leading_metadata():
    lines = [_meta("mode"), *_conversation()]
    out = fork_session_lines(lines, "a1", True, "new-sid")
    assert _types(out)[0] == "mode"
    kept, marker = _split_marker(out)
    assert kept[-1] == "a1"
    assert marker["parentUuid"] == "a1"


def test_fork_marker_parses_as_interrupt_turn():
    """The whole IDLE convergence rests on the real parser classifying the
    appended marker as kind="interrupt" (→ saw_interrupt → IDLE)."""
    from jsonl_parser import parse_session_turns_from_lines
    out = fork_session_lines(_conversation(), "a3", True, "new-sid")
    turns = parse_session_turns_from_lines(out)
    role, _content, _meta, _uuid_, kind, _ts = turns[-1]
    assert (role, kind) == ("system", "interrupt")


def test_fork_marker_inherits_transcript_fields():
    lines = [_line({
        "type": "user", "uuid": "u1", "sessionId": SID,
        "cwd": "/proj", "gitBranch": "master", "version": "2.1.219",
        "userType": "external", "entrypoint": "cli",
        "message": {"role": "user", "content": "hi"},
    }), _assistant("a1", [_text_block("t")])]
    out = fork_session_lines(lines, "a1", True, "new-sid")
    marker = json.loads(out[-1])
    assert marker["cwd"] == "/proj"
    assert marker["gitBranch"] == "master"
    assert marker["version"] == "2.1.219"
