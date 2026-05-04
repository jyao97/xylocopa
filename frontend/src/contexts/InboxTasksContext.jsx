import { createContext, useContext, useState, useEffect, useCallback, useRef, useMemo } from "react";
import { fetchTasksV2, fetchTaskCounts, clog } from "../lib/api";
import { cacheTaskBriefs } from "../lib/detailCache";
import { useWsEvent } from "../hooks/useWebSocket";
import usePageVisible from "../hooks/usePageVisible";

// Single source of truth for the inbox task list + per-perspective counts.
// Replaces the per-TasksPage useState + setInterval pair so split-screen
// panes (and the keep-mounted main TasksPage) all share one fetch and one
// React state — no duplicate polls, no duplicate InboxView mounts re-running
// 28-card render+layout from a fresh array reference.

const INBOX_POLL_MS = 5000;
const COUNTS_POLL_MS = 10000;

const StateContext = createContext(null);
const ActionsContext = createContext(null);

const _fallbackState = { tasks: [], counts: {}, loading: true, version: 0, seeded: false };
const _fallbackActions = { refetch: () => {}, refetchCounts: () => {} };

export function InboxTasksProvider({ children }) {
  const [tasks, setTasks] = useState([]);
  const [counts, setCounts] = useState({});
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const [seeded, setSeeded] = useState(false);
  const visible = usePageVisible();

  const prevHashRef = useRef("");

  const refetch = useCallback(async () => {
    const t0 = performance.now();
    try {
      const data = await fetchTasksV2(`statuses=INBOX&limit=1000`);
      const t1 = performance.now();
      const list = Array.isArray(data) ? data : [];
      cacheTaskBriefs(list);
      // Hash UI-rendered fields so we skip setState on truly no-op polls but
      // still pick up edits (project, model, summary, notify_at, etc).
      // Tasks have no `updated_at` column, so we can't shortcut via a single
      // version stamp — list every field the InboxCard reads.
      const hash = list.map((t) => [
        t.id, t.status, t.sort_order ?? "",
        t.title || "", (t.description || "").length,
        t.project_name || "", t.priority ?? 0,
        t.attempt_number ?? 1, t.agent_summary || "",
        t.retry_context || "", t.model || "", t.effort || "",
        t.skip_permissions ? 1 : 0, t.use_worktree ? 1 : 0,
        t.worktree_name || "", t.notify_at || "", t.deferred_to || "",
      ].join("|")).join(",");
      const changed = hash !== prevHashRef.current;
      prevHashRef.current = hash;
      clog(`[tasks] fetch ${(t1 - t0).toFixed(0)}ms n=${list.length}${changed ? "" : " (no change)"}`);
      if (changed) {
        setTasks(list);
        setVersion((v) => v + 1);
      }
      setSeeded(true);
    } catch (err) {
      console.warn("Failed to load inbox tasks", err);
    } finally {
      setLoading(false);
    }
  }, []);

  const refetchCounts = useCallback(async () => {
    try {
      const data = await fetchTaskCounts();
      setCounts({
        INBOX: data.INBOX ?? 0,
        EXECUTING: (data.QUEUE ?? 0) + (data.ACTIVE ?? 0),
        DONE: data.DONE ?? 0,
        DONE_COMPLETED: data.DONE_COMPLETED ?? 0,
      });
    } catch (err) {
      console.warn("Failed to load task counts", err);
    }
  }, []);

  // Poll while document is visible. One shared poll for everyone.
  useEffect(() => {
    if (!visible) return;
    refetch();
    refetchCounts();
    const t = setInterval(refetch, INBOX_POLL_MS);
    const c = setInterval(refetchCounts, COUNTS_POLL_MS);
    return () => { clearInterval(t); clearInterval(c); };
  }, [visible, refetch, refetchCounts]);

  // WS-driven invalidation
  useWsEvent((event) => {
    if (event.type !== "task_update") return;
    refetch();
    refetchCounts();
  });

  const state = useMemo(() => ({ tasks, counts, loading, version, seeded }), [tasks, counts, loading, version, seeded]);
  const actions = useMemo(() => ({ refetch, refetchCounts }), [refetch, refetchCounts]);

  return (
    <StateContext.Provider value={state}>
      <ActionsContext.Provider value={actions}>
        {children}
      </ActionsContext.Provider>
    </StateContext.Provider>
  );
}

export function useInboxTasks() {
  return useContext(StateContext) || _fallbackState;
}

export function useInboxTasksActions() {
  return useContext(ActionsContext) || _fallbackActions;
}
