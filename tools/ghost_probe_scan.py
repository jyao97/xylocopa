#!/usr/bin/env python3
"""GHOST_DELIVERED probe report — scan logs for GHOST_PROBE lines and summarize.

Run after a >=12h soak following deployment of the GHOST_PROBE instrumentation
(commit 561cd7d, deployed 2026-04-30 11:44 PDT). Writes a markdown report.

Usage: python3 ghost_probe_scan.py [SINCE_ISO] [REPORT_PATH]
  SINCE_ISO defaults to 2026-04-30T11:44:00 (PDT-naive)
  REPORT_PATH defaults to logs/ghost_probe_report_<today>.md
"""
import re
import sys
import os
import glob
from collections import Counter, defaultdict
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")

SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-04-30 11:44:00"
REPORT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    LOGS_DIR,
    f"ghost_probe_report_{datetime.now().strftime('%Y-%m-%d_%H%M')}.md",
)

# Ensure report dir exists
os.makedirs(os.path.dirname(REPORT), exist_ok=True)


def parse_ts(s):
    return datetime.strptime(s.replace(",", "."), "%Y-%m-%d %H:%M:%S.%f")


SINCE_DT = datetime.fromisoformat(SINCE)


def collect_lines():
    """Yield (ts, line) tuples for lines after SINCE."""
    paths = sorted(glob.glob(os.path.join(LOGS_DIR, "orchestrator.log*")))
    for path in paths:
        try:
            with open(path, "r", errors="replace") as f:
                for line in f:
                    if "GHOST_PROBE" not in line:
                        continue
                    try:
                        ts = parse_ts(line.split(" [")[0])
                    except Exception:
                        continue
                    if ts < SINCE_DT:
                        continue
                    yield ts, line.rstrip()
        except FileNotFoundError:
            continue


# Regex per event kind
re_mig1 = re.compile(
    r"GHOST_PROBE migration1_backfill_delivered_at "
    r"msg=(\S+) agent=(\S+) role=(\S+) status=(\S+) source=(\S+) "
    r"display_seq=(\S+) jsonl_uuid=(\S+) created_at=(.+)"
)
re_mig1_total = re.compile(r"GHOST_PROBE migration1: backfilled delivered_at for (\d+) rows")
re_mig2_sent_completed = re.compile(
    r"GHOST_PROBE migration2 SENT→COMPLETED "
    r"msg=(\S+) agent=(\S+) source=(\S+) display_seq=(\S+) "
    r"delivered_at=(\S+) jsonl_uuid=(\S+) created_at=(\S+) "
    r"content=(.+) — flipping"
)
re_mig2_cancel_completed = re.compile(
    r"GHOST_PROBE migration2 CANCELLED→COMPLETED "
    r"msg=(\S+) agent=(\S+) source=(\S+) display_seq=(\S+) "
    r"delivered_at=(\S+) jsonl_uuid=(\S+) content=(.+)"
)
re_mig2_cancel_delete = re.compile(
    r"GHOST_PROBE migration2 CANCELLED_DELETE "
    r"msg=(\S+) agent=(\S+) source=(\S+) content=(.+)"
)
re_mig2_pre_sent = re.compile(
    r"GHOST_PROBE migration2 SENT→pre_sent_zone "
    r"msg=(\S+) agent=(\S+) source=(\S+) delivered_at=(\S+) "
    r"display_seq=(\S+) jsonl_uuid=(\S+) content=(.+)"
)
re_pre_send = re.compile(
    r"GHOST_PROBE pre_send agent=(\S+) msg=(\S+) status=(\S+) gen=(\S+) "
    r"pane_tail=(.+?)(?: send_path=(\S+))?$"
)
re_promote = re.compile(
    r"GHOST_PROBE promote msg=(\S+) agent=(\S+) status=(\S+) gen=(\S+) "
    r"send_path=(\S+) content=(.+?) pane_tail=(.+)$"
)
re_ack_received = re.compile(
    r"GHOST_PROBE ack_received msg=(\S+) agent=(\S+) elapsed=([\d.]+)s"
)
re_ack_missing = re.compile(
    r"GHOST_PROBE ack_missing msg=(\S+) agent=(\S+) after=([\d.]+)s "
    r"status_at_promote=(\S+) gen=(\S+) send_path=(\S+) "
    r"content=(.+?) pane_tail=(.+)$"
)


mig1_rows = []
mig1_total = 0
mig2_sent_completed = []
mig2_cancel_completed = []
mig2_cancel_delete = []
mig2_pre_sent = []
pre_sends = {}  # msg_short → record
promotes = {}  # msg_short → record
ack_received = {}  # msg_short → elapsed
ack_missing = {}  # msg_short → record

for ts, line in collect_lines():
    body = line.split("] ", 2)[-1]
    m = re_mig1.search(body)
    if m:
        mig1_rows.append({
            "ts": ts.isoformat(),
            "msg": m.group(1),
            "agent": m.group(2),
            "role": m.group(3),
            "status": m.group(4),
            "source": m.group(5),
            "display_seq": m.group(6),
            "jsonl_uuid": m.group(7),
            "created_at": m.group(8),
        })
        continue
    m = re_mig1_total.search(body)
    if m:
        mig1_total += int(m.group(1))
        continue
    m = re_mig2_sent_completed.search(body)
    if m:
        mig2_sent_completed.append({
            "ts": ts.isoformat(),
            "msg": m.group(1), "agent": m.group(2), "source": m.group(3),
            "display_seq": m.group(4), "delivered_at": m.group(5),
            "jsonl_uuid": m.group(6), "created_at": m.group(7),
            "content": m.group(8),
        })
        continue
    m = re_mig2_cancel_completed.search(body)
    if m:
        mig2_cancel_completed.append({
            "ts": ts.isoformat(),
            "msg": m.group(1), "agent": m.group(2), "source": m.group(3),
            "display_seq": m.group(4), "delivered_at": m.group(5),
            "jsonl_uuid": m.group(6), "content": m.group(7),
        })
        continue
    m = re_mig2_cancel_delete.search(body)
    if m:
        mig2_cancel_delete.append({
            "ts": ts.isoformat(),
            "msg": m.group(1), "agent": m.group(2), "source": m.group(3),
            "content": m.group(4),
        })
        continue
    m = re_mig2_pre_sent.search(body)
    if m:
        mig2_pre_sent.append({
            "ts": ts.isoformat(),
            "msg": m.group(1), "agent": m.group(2), "source": m.group(3),
            "delivered_at": m.group(4), "display_seq": m.group(5),
            "jsonl_uuid": m.group(6), "content": m.group(7),
        })
        continue
    m = re_pre_send.search(body)
    if m:
        agent, msg, status, gen, pane_tail, send_path = m.groups()
        pre_sends[msg] = {
            "ts": ts.isoformat(), "agent": agent, "msg": msg,
            "status": status, "gen": gen,
            "pane_tail": pane_tail.strip(),
            "send_path": send_path or "dispatch_pending",
        }
        continue
    m = re_promote.search(body)
    if m:
        promotes[m.group(1)] = {
            "ts": ts.isoformat(), "msg": m.group(1), "agent": m.group(2),
            "status": m.group(3), "gen": m.group(4),
            "send_path": m.group(5),
            "content": m.group(6), "pane_tail": m.group(7),
        }
        continue
    m = re_ack_received.search(body)
    if m:
        ack_received[m.group(1)] = float(m.group(3))
        continue
    m = re_ack_missing.search(body)
    if m:
        ack_missing[m.group(1)] = {
            "ts": ts.isoformat(), "msg": m.group(1), "agent": m.group(2),
            "elapsed": float(m.group(3)),
            "status_at_promote": m.group(4), "gen": m.group(5),
            "send_path": m.group(6),
            "content": m.group(7), "pane_tail": m.group(8),
        }
        continue


# Compute correlations
total_promotes = len(pre_sends)
ghost_count = sum(1 for m in ack_missing if m not in ack_received)

# Each ghost = ack_missing without subsequent ack_received
fresh_ghosts = []
for msg, rec in ack_missing.items():
    if msg in ack_received:
        # late ack — not a ghost, just slow
        continue
    fresh_ghosts.append(rec)

# Correlation analysis
status_at_promote_counts = Counter(g["status_at_promote"] for g in fresh_ghosts)
gen_counts = Counter(g["gen"] for g in fresh_ghosts)
send_path_counts = Counter(g["send_path"] for g in fresh_ghosts)


def pane_signature(pane_tail):
    """Reduce a pane_tail snippet to a coarse pattern key."""
    if not pane_tail or pane_tail == "None":
        return "<no_pane>"
    p = pane_tail.lower()
    if "stopped" in p:
        return "stopped_marker"
    if "permission" in p or "allow" in p:
        return "permission_prompt"
    if "ask user question" in p or "askuser" in p:
        return "ask_user_card"
    if "exitplanmode" in p or "plan mode" in p:
        return "plan_mode"
    if "interrupted" in p or "esc to interrupt" in p:
        return "interrupt_marker"
    if re.search(r">\s*$", pane_tail.strip().rstrip("'\"")):
        return "prompt_ready"
    if "│" in pane_tail or "╭" in pane_tail or "╰" in pane_tail:
        return "tui_box_render"
    return "other"


pane_pattern_counts = Counter(pane_signature(g["pane_tail"]) for g in fresh_ghosts)


# Build markdown report
lines = []
ap = lines.append
ap(f"# GHOST_DELIVERED probe report — {datetime.now().isoformat()}")
ap("")
ap(f"Window: since `{SINCE}` (instrumentation deployed at commit 561cd7d).")
ap("")
ap("## TL;DR")
ap("")
ap(f"- Total promote-to-sent events: **{total_promotes}**")
ap(f"- Fresh ghosts (ack_missing without late ack): **{len(fresh_ghosts)}**")
rate = (len(fresh_ghosts) / total_promotes * 100) if total_promotes else 0
ap(f"- Ghost rate: **{rate:.2f}%**")
ap(f"- Migration 1 (delivered_at backfill) row count: **{len(mig1_rows)}** (total: {mig1_total})")
ap(f"- Migration 2 SENT→COMPLETED flips: **{len(mig2_sent_completed)}**")
ap(f"- Migration 2 SENT→pre_sent_zone rescues: **{len(mig2_pre_sent)}**")
ap("")

ap("## Migration 1 — delivered_at backfill")
ap("")
if mig1_rows:
    ap("| ts | msg | agent | role | status | source | display_seq | jsonl_uuid | created_at |")
    ap("|---|---|---|---|---|---|---|---|---|")
    for r in mig1_rows[:200]:
        ap(f"| {r['ts']} | {r['msg']} | {r['agent']} | {r['role']} | {r['status']} | {r['source']} | {r['display_seq']} | {r['jsonl_uuid']} | {r['created_at']} |")
    if len(mig1_rows) > 200:
        ap(f"\n... {len(mig1_rows) - 200} more rows omitted.")
else:
    ap("_No migration1 backfill events fired in this window._")
ap("")

ap("## Migration 2 — SENT→COMPLETED (the headline ghost producer)")
ap("")
if mig2_sent_completed:
    ap("| ts | msg | agent | source | display_seq | delivered_at | jsonl_uuid | content |")
    ap("|---|---|---|---|---|---|---|---|")
    for r in mig2_sent_completed:
        ap(f"| {r['ts']} | {r['msg']} | {r['agent']} | {r['source']} | {r['display_seq']} | {r['delivered_at']} | {r['jsonl_uuid']} | {r['content']} |")
else:
    ap("_No migration2 SENT→COMPLETED flips in this window._")
ap("")

ap("## Migration 2 — SENT→pre_sent_zone (rescued, did not become ghost)")
ap("")
if mig2_pre_sent:
    ap("| ts | msg | agent | source | display_seq | delivered_at | content |")
    ap("|---|---|---|---|---|---|---|")
    for r in mig2_pre_sent:
        ap(f"| {r['ts']} | {r['msg']} | {r['agent']} | {r['source']} | {r['display_seq']} | {r['delivered_at']} | {r['content']} |")
else:
    ap("_No SENT→pre_sent_zone migrations in this window._")
ap("")

ap("## Fresh ghosts (ack_missing without late recovery)")
ap("")
if fresh_ghosts:
    ap("| ts | msg | agent | status_at_promote | gen | send_path | elapsed_s | pane_signature | pane_tail |")
    ap("|---|---|---|---|---|---|---|---|---|")
    for g in fresh_ghosts:
        sig = pane_signature(g["pane_tail"])
        pt = g["pane_tail"][:120].replace("|", "\\|")
        ap(f"| {g['ts']} | {g['msg']} | {g['agent']} | {g['status_at_promote']} | {g['gen']} | {g['send_path']} | {g['elapsed']:.1f} | {sig} | `{pt}` |")
else:
    ap("_No fresh ghosts detected. The instrumentation may not have caught any if no ghost-producing scenarios occurred._")
ap("")

ap("## Correlations across fresh ghosts")
ap("")
ap("**status_at_promote distribution:**")
for k, v in status_at_promote_counts.most_common():
    ap(f"- `{k}`: {v}")
ap("")
ap("**generating_at_promote distribution:**")
for k, v in gen_counts.most_common():
    ap(f"- `{k}`: {v}")
ap("")
ap("**send_path distribution:**")
for k, v in send_path_counts.most_common():
    ap(f"- `{k}`: {v}")
ap("")
ap("**Pane_tail signature distribution at moment of failed send:**")
for k, v in pane_pattern_counts.most_common():
    ap(f"- `{k}`: {v}")
ap("")

ap("## Proposed dispatch-time preconditions")
ap("")
ap("Based on the dominant pane_tail signatures above, the dispatcher's busy guard should be augmented with at least one of:")
ap("")
ap("1. **TUI prompt-readiness probe**: `capture_tmux_pane(...).rstrip().endswith('> ')` (or equivalent input-line pattern) before send-keys. Reject and re-queue if the pane still shows a tool result, partial render, or transient marker.")
ap("2. **Stop-hook settle delay**: enforce 200-500 ms grace after the latest `stop_hook_summary` before allowing dispatch_pending to send.")
ap("3. **ESC settle delay**: enforce 500-800 ms grace after `escape: sent C-c` before dispatch_pending fires.")
ap("4. **Post-restart sync drain confirmation**: require sync_engine to report at least one `no_change` cycle since startup before allowing dispatch_pending — proves JSONL is up to date.")
ap("5. **Hook-ack timeout retry**: if no UserPromptSubmit hook arrives within N seconds of promote, automatically re-promote (bounded retries).")
ap("")
ap("Pick whichever set of preconditions covers the dominant ghost pane_tail signature(s) above.")
ap("")

with open(REPORT, "w") as f:
    f.write("\n".join(lines))

print(f"Report written to {REPORT}")
print(f"Total promotes: {total_promotes}")
print(f"Fresh ghosts: {len(fresh_ghosts)}")
print(f"Migration 1 touches: {len(mig1_rows)}")
print(f"Migration 2 SENT→COMPLETED: {len(mig2_sent_completed)}")
if fresh_ghosts:
    top = pane_pattern_counts.most_common(1)[0]
    print(f"Top pane_tail pattern: {top[0]} ({top[1]})")
