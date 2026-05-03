import { useState, useRef } from "react";

/**
 * Token budget pill — sits between Stop and Monitor in the chat header.
 *
 *   [● 12%]   cyan      <50%
 *   [● 60%]   orange    >=50%   (compact zone — CC auto-compacts ~83%,
 *                                so >=95% red band is unreachable in practice)
 *
 * Click → popover with per-component breakdown + suggestions.
 * Hover → tooltip via title attr with absolute numbers.
 */
export default function ContextUsagePill({ usage, agentId }) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef(null);

  const hasData = !!usage?.has_data;
  const pct = hasData ? Math.max(0, Math.min(999, usage.percent || 0)) : 0;
  const total = hasData ? (usage.total || 0) : 0;
  const limit = usage?.limit || 200_000;

  let chipCls, dotCls;
  if (!hasData) {
    chipCls = "bg-gray-500/15 text-gray-400";
    dotCls = "bg-gray-400";
  } else if (pct < 50) {
    chipCls = "bg-cyan-500/15 text-cyan-600 dark:text-cyan-400";
    dotCls = "bg-cyan-500";
  } else {
    chipCls = "bg-orange-500/15 text-orange-600 dark:text-orange-400";
    dotCls = "bg-orange-500";
  }
  const pulse = false;

  const label = hasData ? `${Math.round(pct)}%` : "—";
  const fmt = (n) => n.toLocaleString();
  const titleText = hasData
    ? `Context: ${fmt(total)} / ${fmt(limit)} tokens (${pct.toFixed(1)}%)`
    : "Context: no data yet";

  return (
    <span className="relative inline-flex">
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={titleText}
        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium transition-colors hover:opacity-80 ${chipCls}`}
      >
        <span className={`inline-block w-1.5 h-1.5 rounded-full ${dotCls} ${pulse ? "animate-pulse" : ""}`} />
        {label}
      </button>
      {open && (
        <ContextUsagePopover
          usage={usage}
          agentId={agentId}
          onClose={() => setOpen(false)}
        />
      )}
    </span>
  );
}

const SEVERITY_STYLES = {
  urgent: "bg-red-500/15 text-red-600 dark:text-red-400 border-l-2 border-red-500",
  warn: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-l-2 border-amber-500",
  info: "bg-cyan-500/15 text-cyan-600 dark:text-cyan-400 border-l-2 border-cyan-500",
};

function ContextUsagePopover({ usage, agentId, onClose }) {
  const [expanded, setExpanded] = useState({});

  // Single source of truth: usage prop already carries components +
  // suggestions, refreshed via WS push on every assistant turn.
  const hasData = !!usage?.has_data;
  const total = hasData ? (usage.total || 0) : 0;
  const limit = usage?.limit || 200_000;
  const free = Math.max(0, limit - total);
  const pct = hasData ? (usage.percent || 0) : 0;
  const fmt = (n) => n.toLocaleString();

  const components = usage?.components || [];
  const suggestions = usage?.suggestions || [];

  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="absolute right-0 top-full mt-1 z-50 w-80 max-w-[90vw] rounded-lg shadow-lg bg-surface border border-divider p-3 text-xs max-h-[70vh] overflow-y-auto">
        <div className="flex items-baseline justify-between mb-1">
          <div className="font-semibold text-body">Context Usage</div>
          {usage?.model && (
            <div className="font-mono text-[10px] text-faint">{usage.model}</div>
          )}
        </div>

        {!hasData ? (
          <div className="text-dim">
            No assistant turns yet — usage will appear after the first response.
          </div>
        ) : (
          <>
            <div className="text-dim mb-2 tabular-nums">
              {fmt(total)} / {fmt(limit)} tokens ({pct.toFixed(1)}%)
            </div>
            <div className="w-full h-2 rounded-full bg-input overflow-hidden mb-3 flex">
              {components.map((c) => {
                const widthPct = Math.min(100, (c.tokens / limit) * 100);
                if (widthPct < 0.1) return null;
                const color = COMPONENT_COLOR[c.name] || "bg-gray-500";
                return (
                  <div
                    key={c.name}
                    className={`h-full ${color}`}
                    style={{ width: `${widthPct}%` }}
                    title={`${c.name}: ${fmt(c.tokens)} (${c.percent.toFixed(1)}%)`}
                  />
                );
              })}
            </div>

            <div className="space-y-1">
              {components.map((c) => (
                <ComponentRow
                  key={c.name}
                  component={c}
                  expanded={!!expanded[c.name]}
                  onToggle={() => setExpanded((s) => ({ ...s, [c.name]: !s[c.name] }))}
                />
              ))}
              <div className="flex items-center justify-between py-1 text-dim border-t border-divider mt-2 pt-2">
                <span>Free space</span>
                <span className="tabular-nums">
                  {fmt(free)}
                  <span className="text-faint ml-1">({((free / limit) * 100).toFixed(1)}%)</span>
                </span>
              </div>
            </div>

            {suggestions.length > 0 && (
              <div className="mt-3 pt-2 border-t border-divider">
                <div className="font-semibold text-body mb-1.5">Suggestions</div>
                <div className="space-y-1">
                  {suggestions.map((s, i) => (
                    <div key={i} className={`px-2 py-1.5 rounded text-[11px] leading-snug ${SEVERITY_STYLES[s.severity] || SEVERITY_STYLES.info}`}>
                      {s.text}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {usage?.lifetime && (
              <LifetimeSection lifetime={usage.lifetime} />
            )}
          </>
        )}
      </div>
    </>
  );
}

function LifetimeSection({ lifetime }) {
  const [expanded, setExpanded] = useState(false);
  const fmtTok = (n) => {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return String(n);
  };
  const cost = lifetime.estimated_cost_usd || 0;
  const fmtCost = (c) => c >= 100 ? `$${c.toFixed(0)}` : c >= 1 ? `$${c.toFixed(2)}` : `$${c.toFixed(4)}`;
  const sc = lifetime.session_count || 0;
  const hc = lifetime.history_session_count || 0;
  const sessionStr = sc <= 1 ? "current session" : `${sc} CC sessions (${hc} historical)`;
  const byKind = lifetime.by_kind || {};
  const pricing = lifetime.pricing_per_million || {};
  const ccSessions = Array.isArray(lifetime.cc_sessions) ? lifetime.cc_sessions : null;

  return (
    <div className="mt-3 pt-2 border-t border-divider">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between py-0.5 hover:bg-input rounded px-1 -mx-1"
      >
        <span className="flex items-center gap-1.5">
          <span className="font-semibold text-body">Xylo session lifetime</span>
          <svg className={`w-3 h-3 text-dim transition-transform ${expanded ? "rotate-90" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
        </span>
        <span className="tabular-nums text-body">
          {fmtTok(lifetime.total_tokens || 0)} <span className="text-dim">·</span> {fmtCost(cost)}
        </span>
      </button>
      <div className="text-[10px] text-faint pl-1">across {sessionStr} · {lifetime.turn_count || 0} turns</div>

      {expanded && (
        <div className="mt-1.5 ml-1 space-y-0.5 text-[10px]">
          <LifetimeRow label="Input (fresh)"  tokens={byKind.input_tokens}                rate={pricing.input}        />
          <LifetimeRow label="Cache write"    tokens={byKind.cache_creation_input_tokens} rate={pricing.cache_create} />
          <LifetimeRow label="Cache read"     tokens={byKind.cache_read_input_tokens}     rate={pricing.cache_read}   />
          <LifetimeRow label="Output"         tokens={byKind.output_tokens}               rate={pricing.output}       />
          <div className="text-faint italic mt-1.5 leading-snug">
            Token counts come directly from Claude Code's session JSONL — each
            value is what Anthropic's tokenizer reported in the API response.
            Cache reads are billed every turn (at the discounted rate). The
            cached prefix gets re-read on each request, so cache_read tokens
            accumulate well past the current window size.
          </div>
          {lifetime.pricing_model && (
            <div className="text-faint italic mt-1">
              Pricing for {lifetime.pricing_model} (USD/M tokens)
            </div>
          )}

          {ccSessions && (
            <BySessionList
              sessions={ccSessions}
              fmtTok={fmtTok}
              fmtCost={fmtCost}
            />
          )}
        </div>
      )}
    </div>
  );
}

const END_REASON_STYLE = {
  active:        { dot: "bg-emerald-500", label: "active" },
  rotation:      { dot: "bg-cyan-500",    label: "rotation" },
  compact:       { dot: "bg-cyan-500",    label: "compact" },
  clear:         { dot: "bg-cyan-500",    label: "clear" },
  reconciled:    { dot: "bg-cyan-500",    label: "reconciled" },
  subagent_done: { dot: "bg-violet-500",  label: "subagent" },
  stopped:       { dot: "bg-gray-500",    label: "stopped" },
  error:         { dot: "bg-gray-500",    label: "error" },
};

function _endReasonStyle(reason) {
  return END_REASON_STYLE[reason] || { dot: "bg-gray-400", label: reason || "unknown" };
}

function _shortId(id) {
  if (!id) return "";
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

function BySessionList({ sessions, fmtTok, fmtCost }) {
  const [open, setOpen] = useState(false);
  // Tally tokens + cost across both top-level CC sessions and their sub-sessions.
  const totalTurns = sessions.reduce((acc, s) => {
    const subTurns = (s.sub_sessions || []).reduce((a, x) => a + (x.turn_count || 0), 0);
    return acc + (s.turn_count || 0) + subTurns;
  }, 0);
  const totalSubCount = sessions.reduce((acc, s) => acc + (s.sub_sessions?.length || 0), 0);
  const topCount = sessions.length;
  const totalCount = topCount + totalSubCount;
  const summaryStr = totalSubCount === 0
    ? `${topCount} CC session${topCount === 1 ? "" : "s"} · ${totalTurns} turns`
    : `${topCount} top + ${totalSubCount} sub · ${totalTurns} turns`;

  return (
    <div className="mt-2 pt-1.5 border-t border-divider">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between py-0.5 hover:bg-input rounded px-1 -mx-1"
      >
        <span className="flex items-center gap-1.5">
          <span className="font-semibold text-body">CC sessions</span>
          <svg className={`w-3 h-3 text-dim transition-transform ${open ? "rotate-90" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
        </span>
        <span className="text-[10px] text-dim tabular-nums">{summaryStr}</span>
      </button>
      {open && (
        <div className="mt-1 space-y-0.5">
          {sessions.length === 0 && (
            <div className="text-[10px] text-faint italic px-1">No sessions persisted yet.</div>
          )}
          {sessions.map((s) => (
            <SessionRow
              key={s.session_id}
              session={s}
              fmtTok={fmtTok}
              fmtCost={fmtCost}
              depth={0}
            />
          ))}
          <div className="text-faint italic mt-1.5 leading-snug">
            One xylo session can span multiple CC sessions (rotated by /compact
            or /clear) plus any Agent-tool sub-sessions spawned from within.
          </div>
        </div>
      )}
    </div>
  );
}

function SessionRow({ session, fmtTok, fmtCost, depth }) {
  const subs = Array.isArray(session.sub_sessions) ? session.sub_sessions : [];
  const hasSubs = subs.length > 0;
  const [open, setOpen] = useState(false);
  const style = _endReasonStyle(session.end_reason);
  const tokens = session.total_tokens != null
    ? session.total_tokens
    : Object.values(session.totals || {}).reduce((a, b) => a + (b || 0), 0);
  const cost = session.cost_usd || 0;
  const turns = session.turn_count || 0;
  const indent = depth === 0 ? "" : "ml-3 pl-2 border-l border-divider";
  const subTotalTokens = subs.reduce((a, s) =>
    a + (s.total_tokens != null ? s.total_tokens : Object.values(s.totals || {}).reduce((x, y) => x + (y || 0), 0)),
    0,
  );
  const subTotalCost = subs.reduce((a, s) => a + (s.cost_usd || 0), 0);

  return (
    <div className={indent}>
      <button
        type="button"
        onClick={hasSubs ? () => setOpen((v) => !v) : undefined}
        disabled={!hasSubs}
        className={`w-full flex items-center justify-between gap-1 py-0.5 px-1 -mx-1 rounded ${hasSubs ? "hover:bg-input" : ""}`}
        title={`${session.session_id} (${style.label})`}
      >
        <span className="flex items-center gap-1.5 min-w-0">
          <span className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${style.dot}`} />
          <span className="font-mono text-[10px] text-body truncate">{_shortId(session.session_id)}</span>
          <span className="text-faint text-[10px] shrink-0">({style.label})</span>
          {hasSubs && (
            <svg className={`w-2.5 h-2.5 shrink-0 text-dim transition-transform ${open ? "rotate-90" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          )}
        </span>
        <span className="tabular-nums text-[10px] text-dim shrink-0">
          {fmtTok(tokens)} <span className="text-faint">·</span> {fmtCost(cost)} <span className="text-faint">·</span> {turns}<span className="text-faint">⌶</span>
        </span>
      </button>
      {hasSubs && !open && (
        <div className="ml-3.5 text-[10px] text-faint pl-2">
          ▸ {subs.length} sub{subs.length === 1 ? "" : "s"} ({fmtTok(subTotalTokens)} · {fmtCost(subTotalCost)})
        </div>
      )}
      {hasSubs && open && (
        <div className="mt-0.5 space-y-0.5">
          {subs.map((sub) => (
            <SessionRow
              key={sub.session_id}
              session={sub}
              fmtTok={fmtTok}
              fmtCost={fmtCost}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function LifetimeRow({ label, tokens, rate }) {
  const t = tokens || 0;
  const r = rate || 0;
  const cost = (t * r) / 1_000_000;
  const fmt = (n) => n.toLocaleString();
  const fmtCost = (c) => c >= 1 ? `$${c.toFixed(2)}` : `$${c.toFixed(4)}`;
  return (
    <div className="flex items-center justify-between text-dim">
      <span>{label} <span className="text-faint">@ ${r.toFixed(2)}</span></span>
      <span className="tabular-nums">
        {fmt(t)} <span className="text-faint">·</span> {fmtCost(cost)}
      </span>
    </div>
  );
}

const COMPONENT_COLOR = {
  "Messages": "bg-cyan-500",
  "MCP tools": "bg-violet-500",
  "Memory files": "bg-amber-500",
  "Custom Agents": "bg-emerald-500",
  "System overhead": "bg-gray-500",
};

function ComponentRow({ component, expanded, onToggle }) {
  const fmt = (n) => n.toLocaleString();
  const hasBreakdown = Array.isArray(component.breakdown) && component.breakdown.length > 0;
  const dotColor = COMPONENT_COLOR[component.name] || "bg-gray-500";

  return (
    <div>
      <button
        type="button"
        onClick={hasBreakdown ? onToggle : undefined}
        disabled={!hasBreakdown}
        className={`w-full flex items-center justify-between py-1 ${hasBreakdown ? "hover:bg-input rounded px-1 -mx-1" : ""}`}
      >
        <span className="flex items-center gap-1.5 min-w-0">
          <span className={`inline-block w-2 h-2 rounded-sm shrink-0 ${dotColor}`} />
          <span className="truncate text-body">{component.name}</span>
          {hasBreakdown && (
            <svg className={`w-3 h-3 shrink-0 text-dim transition-transform ${expanded ? "rotate-90" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          )}
        </span>
        <span className="tabular-nums text-dim shrink-0">
          {fmt(component.tokens)}
          <span className="text-faint ml-1">({component.percent.toFixed(1)}%)</span>
        </span>
      </button>
      {expanded && hasBreakdown && (
        <div className="ml-3.5 my-1 space-y-0.5 border-l border-divider pl-2">
          {component.breakdown.map((b, i) => (
            <div key={i} className="flex items-center justify-between text-[10px] text-dim">
              <span className="truncate font-mono">{b.name}</span>
              <span className="tabular-nums shrink-0 ml-2">
                {fmt(b.tokens)}
                {b.estimated && <span className="text-faint ml-0.5">~</span>}
              </span>
            </div>
          ))}
          {component.info && (
            <div className="text-[10px] text-faint italic mt-1">{component.info}</div>
          )}
        </div>
      )}
    </div>
  );
}
