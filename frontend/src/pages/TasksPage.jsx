import { useState, useEffect, useCallback, useRef, useMemo, useLayoutEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { dispatchTask, cancelTask, batchProcessTasks, clog } from "../lib/api";
import PageHeader from "../components/PageHeader";
import useWebSocket, { registerViewingTasks, unregisterViewingTasks } from "../hooks/useWebSocket";
import { useToast } from "../contexts/ToastContext";
import { useInboxTasks, useInboxTasksActions } from "../contexts/InboxTasksContext";
import InboxView from "./tasks/InboxView";
import { CardSwipeContext } from "../components/cards/CardShell";
import { forwardState } from "../lib/nav";

export default function TasksPage({ theme, onToggleTheme, isActive = true }) {
  const navigate = useNavigate();
  const location = useLocation();
  // Read shared list/counts from context — provider owns polling + WS sync.
  const { tasks, counts, loading } = useInboxTasks();
  const { refetch: loadTasks, refetchCounts: loadCounts } = useInboxTasksActions();
  useWebSocket(); // ensure connection is alive

  // --- Multi-select state ---
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [expandedTaskId, setExpandedTaskId] = useState(() => {
    try { return localStorage.getItem("inbox:expandedTaskId") || null; } catch { return null; }
  });
  const [actionLoading, setActionLoading] = useState(false);

  // Scroll position persistence
  const inboxScrollRef = useRef(null);
  const scrollSaveTimer = useRef(null);
  const scrollRestored = useRef(false);
  const SCROLL_SAVE_DEBOUNCE = 200;


  const enterSelectMode = useCallback((preSelectId) => {
    setSelecting(true);
    setSelected(preSelectId ? new Set([preSelectId]) : new Set());
    setExpandedTaskId(null);
  }, []);

  const exitSelectMode = useCallback(() => {
    setSelecting(false);
    setSelected(new Set());
  }, []);

  const toggleOne = useCallback((id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelected(new Set(tasks.map((t) => t.id)));
  }, [tasks]);

  const deselectAll = useCallback(() => {
    setSelected(new Set());
  }, []);

  const allSelected = tasks.length > 0 && selected.size === tasks.length;

  const handleExpandTask = useCallback((taskId) => {
    setExpandedTaskId((prev) => {
      const next = prev === taskId ? null : taskId;
      try {
        if (next) localStorage.setItem("inbox:expandedTaskId", next);
        else localStorage.removeItem("inbox:expandedTaskId");
      } catch { /* ignore */ }
      return next;
    });
  }, []);

  // Register viewing for notification suppression — track isActive transitions,
  // not mount/unmount, since this page is kept mounted across tab switches.
  useEffect(() => {
    if (!isActive) return;
    registerViewingTasks();
    return () => unregisterViewingTasks();
  }, [isActive]);

  const onRefresh = useCallback(() => {
    loadTasks();
    loadCounts();
  }, [loadTasks, loadCounts]);


  const toast = useToast();

  const showToast = useCallback((message, type = "success") => {
    if (type === "error") toast.error(message);
    else toast.success(message);
  }, [toast]);

  // --- Bulk action handlers ---
  const dispatchableCount = useMemo(() => {
    return tasks.filter((t) => selected.has(t.id) && t.project_name).length;
  }, [tasks, selected]);

  const handleBulkStart = useCallback(async () => {
    const dispatchable = tasks.filter((t) => selected.has(t.id) && t.project_name);
    if (dispatchable.length === 0 || actionLoading) return;
    setActionLoading(true);
    try {
      await Promise.all(dispatchable.map(t => dispatchTask(t.id)));
      showToast(`Started ${dispatchable.length} task${dispatchable.length > 1 ? "s" : ""}`);
      exitSelectMode();
      onRefresh();
    } catch (err) {
      showToast(`Start failed: ${err.message}`, "error");
    } finally {
      setActionLoading(false);
    }
  }, [tasks, selected, actionLoading, exitSelectMode, showToast, onRefresh]);

  // --- AI batch process (triage agent) ---
  const [batchProcessing, setBatchProcessing] = useState(false);

  const handleBatchProcess = useCallback(async (taskIds) => {
    if (batchProcessing) return;
    setBatchProcessing(true);
    try {
      const res = await batchProcessTasks(taskIds);
      if (res.agent_id) {
        if (selecting) exitSelectMode();
        navigate(`/agents/${res.agent_id}`, { state: forwardState(location) });
      } else {
        showToast(res.message || "No tasks to process");
      }
    } catch (err) {
      showToast(`Batch process failed: ${err.message}`, "error");
    } finally {
      setBatchProcessing(false);
    }
  }, [batchProcessing, selecting, exitSelectMode, showToast, navigate]);

  const handleBulkDelete = useCallback(async () => {
    if (selected.size === 0 || actionLoading) return;
    if (!confirm(`Delete ${selected.size} task${selected.size > 1 ? "s" : ""}?`)) return;
    setActionLoading(true);
    try {
      await Promise.all([...selected].map(id => cancelTask(id)));
      showToast(`Deleted ${selected.size} task${selected.size > 1 ? "s" : ""}`);
      exitSelectMode();
      onRefresh();
    } catch (err) {
      showToast(`Delete failed: ${err.message}`, "error");
    } finally {
      setActionLoading(false);
    }
  }, [selected, actionLoading, exitSelectMode, showToast, onRefresh]);

  // Debounced scroll position save
  const handleInboxScroll = useCallback(() => {
    const el = inboxScrollRef.current;
    if (!el) return;
    clearTimeout(scrollSaveTimer.current);
    scrollSaveTimer.current = setTimeout(() => {
      try { localStorage.setItem("inbox:scrollTop", String(el.scrollTop)); } catch { /* ignore */ }
    }, SCROLL_SAVE_DEBOUNCE);
  }, []);

  // Save scroll position on unmount
  useEffect(() => {
    return () => {
      clearTimeout(scrollSaveTimer.current);
      const el = inboxScrollRef.current;
      if (el) {
        try { localStorage.setItem("inbox:scrollTop", String(el.scrollTop)); } catch { /* ignore */ }
      }
    };
  }, []);

  // Restore scroll position after first load
  useLayoutEffect(() => {
    if (loading || tasks.length === 0 || scrollRestored.current) return;
    scrollRestored.current = true;
    try {
      const savedPos = localStorage.getItem("inbox:scrollTop");
      const savedCount = localStorage.getItem("inbox:taskCount");
      if (savedPos && savedCount && Number(savedCount) === tasks.length) {
        const el = inboxScrollRef.current;
        if (el) el.scrollTop = Number(savedPos);
      }
    } catch { /* ignore */ }
  }, [loading, tasks.length]);

  // Keep saved task count in sync for future visits
  useEffect(() => {
    if (!loading && tasks.length > 0) {
      try { localStorage.setItem("inbox:taskCount", String(tasks.length)); } catch { /* ignore */ }
    }
  }, [loading, tasks.length]);

  // Clear expanded task if the task no longer exists in the list
  useEffect(() => {
    if (!loading && expandedTaskId && tasks.length > 0 && !tasks.some(t => t.id === expandedTaskId)) {
      setExpandedTaskId(null);
      try { localStorage.removeItem("inbox:expandedTaskId"); } catch { /* ignore */ }
    }
  }, [loading, tasks, expandedTaskId]);

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title="Inbox"
        theme={theme}
        onToggleTheme={onToggleTheme}
        showQueueButton
        hideMonitor
        actions={!selecting ? (
          <button
            type="button"
            onClick={() => handleBatchProcess()}
            disabled={batchProcessing}
            title="AI batch process — refine prompts & assign projects"
            className={`h-7 px-2.5 flex items-center gap-1.5 rounded-full text-[11px] font-semibold transition-colors ${
              batchProcessing
                ? "bg-cyan-500 text-white animate-pulse"
                : "bg-cyan-500/15 text-cyan-500 dark:text-cyan-400 hover:bg-cyan-500/25 active:scale-95"
            }`}
          >
            <Sparkles className="w-3.5 h-3.5" />
            AI
          </button>
        ) : undefined}
      >
        {selecting && (
          <div className="grid grid-cols-3 items-center px-4 pb-2">
            <button
              type="button"
              onClick={allSelected ? deselectAll : selectAll}
              className="justify-self-start text-sm font-medium text-cyan-400 hover:text-cyan-300 transition-colors px-2 py-1"
            >
              {allSelected ? "Deselect All" : "Select All"}
            </button>
            <span className="justify-self-center text-sm text-label">
              {selected.size > 0 ? `${selected.size} selected` : "Select items"}
            </span>
            <button
              type="button"
              onClick={exitSelectMode}
              className="justify-self-end text-sm font-semibold text-cyan-400 hover:text-cyan-300 transition-colors px-2 py-1"
            >
              Done
            </button>
          </div>
        )}
      </PageHeader>

      <div
        ref={inboxScrollRef}
        className="flex-1 overflow-y-auto overflow-x-hidden"
        onScroll={handleInboxScroll}
        onClick={(e) => {
          if (expandedTaskId && !e.target.closest("[data-card]")) handleExpandTask(expandedTaskId);
        }}
      >
        <div className="max-w-2xl mx-auto w-full">
          <CardSwipeContext.Provider value={null}>
            <div className="pb-24 px-4 py-3">
              {!loading && (
                <InboxView
                  tasks={tasks}
                  loading={loading}
                  onRefresh={onRefresh}
                  selecting={selecting}
                  selected={selected}
                  onToggle={toggleOne}
                  onEnterSelect={enterSelectMode}
                  expandedTaskId={expandedTaskId}
                  onExpandTask={handleExpandTask}
                />
              )}
            </div>
          </CardSwipeContext.Provider>
        </div>
      </div>

      {/* ── Bulk Action Bar ── */}
      {selecting && selected.size > 0 && (
        <div className="fixed bottom-20 left-0 right-0 z-20 px-4 pb-2 animate-bar-slide-up">
          <div className="max-w-xl mx-auto bg-surface border border-divider rounded-xl shadow-lg p-3 flex items-center justify-center gap-3">
            {/* AI batch process selected */}
            <button
              type="button"
              onClick={() => handleBatchProcess([...selected])}
              disabled={batchProcessing || actionLoading}
              className={`flex-1 flex items-center justify-center gap-2 min-h-[40px] rounded-lg text-sm font-medium text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                batchProcessing
                  ? "bg-gradient-to-r from-cyan-500 to-blue-500 animate-pulse"
                  : "bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500"
              }`}
            >
              <Sparkles className="w-4 h-4" />
              AI {selected.size}
            </button>
            {/* Start */}
            <button
              type="button"
              onClick={handleBulkStart}
              disabled={actionLoading || dispatchableCount === 0}
              className="flex-1 flex items-center justify-center gap-2 min-h-[40px] rounded-lg bg-cyan-600 text-white text-sm font-medium hover:bg-cyan-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
              </svg>
              {dispatchableCount > 0 ? `Start ${dispatchableCount}` : "Start"}
            </button>
            {/* Delete */}
            <button
              type="button"
              onClick={handleBulkDelete}
              disabled={actionLoading}
              className="flex-1 flex items-center justify-center gap-2 min-h-[40px] rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
              Delete {selected.size}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
