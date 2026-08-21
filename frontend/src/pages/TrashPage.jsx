import { useState, useEffect, useCallback, memo } from "react";
import { useNavigate } from "react-router-dom";
import { fetchTrashFolders, deleteTrashFolder, restoreTrashFolder } from "../lib/api";
import PageHeader from "../components/PageHeader";
import useLongPress from "../hooks/useLongPress";

const TrashRow = memo(function TrashRow({ folder, busy, onRestore, onDelete, selecting, selected, onToggle, onEnterSelect }) {
  const handleClick = () => {
    if (selecting) onToggle?.(folder.name);
  };

  const isInner = (e) => !!e?.target?.closest?.("[data-no-longpress]");
  const longPressHandlers = useLongPress((e) => {
    if (selecting) return;
    if (isInner(e)) return;
    if (navigator.vibrate) navigator.vibrate(15);
    onEnterSelect?.(folder.name);
  }, (e) => {
    if (isInner(e)) return;
    handleClick();
  });

  return (
    <button
      type="button"
      {...longPressHandlers}
      style={{ WebkitTapHighlightColor: "transparent" }}
      className={`w-full text-left flex items-center justify-between rounded-xl bg-surface shadow-card overflow-hidden px-5 py-4 transform-gpu transition-[transform,box-shadow,ring-color,opacity,background-color,filter] duration-400 ease-[cubic-bezier(0.22,1.15,0.36,1)] active:bg-input focus:outline-none focus-visible:ring-2 focus-visible:ring-accent hover:ring-1 hover:ring-ring-hover ${
        selecting && selected ? "ring-2 ring-accent-50 brightness-[0.88]" : ""
      }`}
    >
      <div className="min-w-0">
        <h3 className="text-sm font-medium text-label truncate">{folder.name}</h3>
      </div>
      {!selecting && (
        <div className="shrink-0 flex items-center gap-2 ml-4">
          <button
            type="button"
            data-no-longpress
            disabled={busy === folder.name}
            onClick={(e) => { e.stopPropagation(); onRestore(folder.name); }}
            className="px-3 py-1.5 text-xs font-semibold rounded-lg accent-tint-20 text-accent hover:accent-tint-25 disabled:opacity-50 transition-colors"
          >
            {busy === folder.name ? "..." : "Restore"}
          </button>
          <button
            type="button"
            data-no-longpress
            disabled={busy === folder.name}
            onClick={(e) => { e.stopPropagation(); onDelete(folder.name); }}
            className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-red-600/20 text-red-400 hover:bg-red-600/30 disabled:opacity-50 transition-colors"
          >
            {busy === folder.name ? "..." : "Delete"}
          </button>
        </div>
      )}
    </button>
  );
});

export default function TrashPage({ theme, onToggleTheme }) {
  const navigate = useNavigate();
  const [folders, setFolders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);

  // Multi-select state
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);

  const enterSelectMode = useCallback((preSelectName) => {
    setSelecting(true);
    setSelected(preSelectName ? new Set([preSelectName]) : new Set());
  }, []);
  const exitSelectMode = useCallback(() => {
    setSelecting(false);
    setSelected(new Set());
  }, []);
  const toggleOne = useCallback((name) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  const load = async () => {
    try {
      const data = await fetchTrashFolders();
      setFolders(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleRestore = async (name) => {
    setBusy(name);
    try {
      await restoreTrashFolder(name);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  };

  const handleDelete = async (name) => {
    if (!window.confirm(`Permanently delete "${name}"? This cannot be undone.`)) return;
    setBusy(name);
    try {
      await deleteTrashFolder(name);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  };

  const allSelected = folders.length > 0 && selected.size === folders.length;
  const selectAll = useCallback(() => setSelected(new Set(folders.map((f) => f.name))), [folders]);
  const deselectAll = useCallback(() => setSelected(new Set()), []);

  const runBulk = useCallback(async (fn, verb) => {
    if (selected.size === 0 || bulkBusy) return;
    setBulkBusy(true);
    let ok = 0, failed = 0;
    for (const n of selected) {
      try { await fn(n); ok++; } catch { failed++; }
    }
    setBulkBusy(false);
    if (failed > 0) setError(`${verb} ${ok}, failed ${failed}`);
    exitSelectMode();
    await load();
    window.dispatchEvent(new CustomEvent("projects-data-changed"));
  }, [selected, bulkBusy, exitSelectMode]);

  const handleBulkRestore = useCallback(() => {
    runBulk(restoreTrashFolder, "Restored");
  }, [runBulk]);

  const handleBulkDelete = useCallback(() => {
    if (selected.size === 0) return;
    if (!window.confirm(`Permanently delete ${selected.size} project${selected.size > 1 ? "s" : ""}? This cannot be undone.`)) return;
    runBulk(deleteTrashFolder, "Deleted");
  }, [selected, runBulk]);

  const backButton = (
    <button
      type="button"
      onClick={() => navigate("/projects")}
      className="p-2 rounded-lg text-label hover:text-heading hover:bg-input transition-colors"
    >
      <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
      </svg>
    </button>
  );

  return (
    <div className="h-full flex flex-col">
      <PageHeader title="Deleted Projects" theme={theme} onToggleTheme={onToggleTheme} actions={!selecting ? backButton : undefined}>
        {selecting && (
          <div className="grid grid-cols-3 items-center px-4 pb-2">
            <button
              type="button"
              onClick={allSelected ? deselectAll : selectAll}
              className="justify-self-start text-sm font-medium text-accent hover:opacity-80 transition-colors px-2 py-1"
            >
              {allSelected ? "Deselect All" : "Select All"}
            </button>
            <span className="justify-self-center text-sm text-label">
              {selected.size > 0 ? `${selected.size} selected` : "Select projects"}
            </span>
            <button
              type="button"
              onClick={exitSelectMode}
              className="justify-self-end text-sm font-semibold text-accent hover:opacity-80 transition-colors px-2 py-1"
            >
              Done
            </button>
          </div>
        )}
      </PageHeader>
      <div className="flex-1 overflow-y-auto overflow-x-hidden">
      <div className="pb-24 p-4 max-w-2xl mx-auto w-full">

        {loading && (
          <div className="flex justify-center py-12">
            <span className="text-dim text-sm animate-pulse">Loading...</span>
          </div>
        )}

        {error && (
          <div className="bg-red-950/40 border border-red-800 rounded-xl p-4 mb-4">
            <p className="text-red-400 text-sm">{error}</p>
          </div>
        )}

        {!loading && !error && folders.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-faint">
            <svg className="w-12 h-12 mb-3" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
            <p className="text-sm">Trash is empty</p>
          </div>
        )}

        <div className="space-y-2">
          {folders.map((folder) => (
            <TrashRow
              key={folder.name}
              folder={folder}
              busy={busy}
              onRestore={handleRestore}
              onDelete={handleDelete}
              selecting={selecting}
              selected={selected.has(folder.name)}
              onToggle={toggleOne}
              onEnterSelect={enterSelectMode}
            />
          ))}
        </div>
      </div>
      </div>

      {selecting && selected.size > 0 && (
        <div className="fixed bottom-20 left-0 right-0 z-20 px-4 pb-2 animate-bar-slide-up">
          <div className="max-w-xl mx-auto bg-surface border border-divider rounded-xl shadow-lg p-3 flex items-center justify-center gap-3">
            <button
              type="button"
              onClick={handleBulkRestore}
              disabled={bulkBusy}
              className="flex-1 flex items-center justify-center gap-2 min-h-[40px] rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              {bulkBusy ? "..." : `Restore ${selected.size}`}
            </button>
            <button
              type="button"
              onClick={handleBulkDelete}
              disabled={bulkBusy}
              className="flex-1 flex items-center justify-center gap-2 min-h-[40px] rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
              {bulkBusy ? "..." : `Delete ${selected.size}`}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
