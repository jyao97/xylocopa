import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import {
  fetchDropboxFolders,
  startDropboxDryRun,
  fetchDropboxDryRun,
  stopDropboxDryRun,
  updateProjectSettings,
  triggerDropboxSync,
} from "../../lib/api";
import { formatBytes } from "./format";

const POLL_INTERVAL = 1500;
const LARGE_BYTES = 20 * 1024 * 1024 * 1024; // 20 GB
const BUDGET_FILES = 300000;

/**
 * Folder picker modal for Dropbox sync configuration.
 * Shows top-level entries with dry-run stats, lets the user select which
 * folders to sync, then saves the config.
 */
export default function DropboxFolderPicker({
  open,
  project,
  remoteRoot,
  initialFolders,
  initialIgnore,
  onClose,
  onSaved,
  syncAfterSave = true,
}) {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(new Set());
  const [dryRunStats, setDryRunStats] = useState({});
  const [dryRunTotal, setDryRunTotal] = useState(null);
  const [dryRunStatus, setDryRunStatus] = useState("idle"); // idle | running | complete | error
  const [dryRunError, setDryRunError] = useState("");
  const [ignoreText, setIgnoreText] = useState(initialIgnore || "");
  const [showIgnore, setShowIgnore] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [syncNote, setSyncNote] = useState("");

  const jobIdRef = useRef(null);
  const pollRef = useRef(null);

  // Selectable entries: not default_ignored and not symlink
  const selectableEntries = useMemo(
    () => entries.filter((e) => !e.default_ignored && e.type !== "symlink"),
    [entries],
  );

  const allSelected = useMemo(
    () => selectableEntries.length > 0 && selectableEntries.every((e) => selected.has(e.name)),
    [selectableEntries, selected],
  );

  // Selected totals from dry-run stats
  const selectedTotals = useMemo(() => {
    let files = 0;
    let bytes = 0;
    for (const name of selected) {
      const s = dryRunStats[name];
      if (s) {
        files += s.files || 0;
        bytes += s.bytes || 0;
      }
    }
    return { files, bytes };
  }, [selected, dryRunStats]);

  // Stop polling helper
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // Stop dry run on server
  const stopDryRun = useCallback(async () => {
    stopPolling();
    const jid = jobIdRef.current;
    if (jid) {
      try {
        await stopDropboxDryRun(jid);
      } catch {
        // best effort
      }
      jobIdRef.current = null;
    }
  }, [stopPolling]);

  // Load entries + kick off dry run
  useEffect(() => {
    if (!open) return;

    let cancelled = false;

    setEntries([]);
    setLoading(true);
    setSelected(new Set());
    setDryRunStats({});
    setDryRunTotal(null);
    setDryRunStatus("idle");
    setDryRunError("");
    setIgnoreText(initialIgnore || "");
    setShowIgnore(false);
    setSaving(false);
    setSaveError("");
    setSyncNote("");
    jobIdRef.current = null;

    (async () => {
      try {
        // Fetch folder entries
        const res = await fetchDropboxFolders(project);
        if (cancelled) return;
        const list = res.entries || [];
        setEntries(list);

        // Initialize selection from initialFolders
        const selectable = list.filter((e) => !e.default_ignored && e.type !== "symlink");
        if (initialFolders === null || initialFolders === undefined) {
          setSelected(new Set(selectable.map((e) => e.name)));
        } else {
          const init = new Set(initialFolders);
          setSelected(new Set(selectable.filter((e) => init.has(e.name)).map((e) => e.name)));
        }
        setLoading(false);

        // Start dry run (no folders filter so we get per-entry stats for everything)
        const dr = await startDropboxDryRun(project);
        if (cancelled) return;
        jobIdRef.current = dr.job_id;
        setDryRunStatus("running");

        // Poll dry run
        pollRef.current = setInterval(async () => {
          try {
            const status = await fetchDropboxDryRun(dr.job_id);
            if (cancelled) return;
            if (status.entries) setDryRunStats(status.entries);
            if (status.total) setDryRunTotal(status.total);
            if (status.status === "complete" || status.status === "error") {
              stopPolling();
              setDryRunStatus(status.status);
              if (status.error) setDryRunError(status.error);
            }
          } catch {
            // transient poll error, keep trying
          }
        }, POLL_INTERVAL);
      } catch (err) {
        if (!cancelled) {
          setLoading(false);
          setDryRunStatus("error");
          setDryRunError(err.message || "Failed to load folders");
        }
      }
    })();

    return () => {
      cancelled = true;
      stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, project]);

  // On close: stop dry run if still running
  const handleClose = useCallback(() => {
    if (dryRunStatus === "running") {
      stopDryRun();
    }
    onClose();
  }, [dryRunStatus, stopDryRun, onClose]);

  // Escape to close
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (e.key === "Escape") handleClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, handleClose]);

  // Body scroll lock
  useEffect(() => {
    if (!open) return;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; };
  }, [open]);

  const toggleOne = useCallback((name) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  const handleSelectAll = useCallback(() => {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(selectableEntries.map((e) => e.name)));
    }
  }, [allSelected, selectableEntries]);

  const handleSave = useCallback(async () => {
    if (selected.size === 0) return;
    setSaving(true);
    setSaveError("");
    setSyncNote("");
    try {
      const folders = [...selected].sort();
      const ignore = ignoreText.trim() || null;
      const updated = await updateProjectSettings(project, {
        dropbox_sync: true,
        dropbox_folders: folders,
        dropbox_ignore: ignore,
      });

      if (syncAfterSave) {
        try {
          await triggerDropboxSync(project);
          setSyncNote("Sync queued.");
        } catch {
          // ignore sync trigger errors
        }
      }

      // Stop dry run before closing
      if (dryRunStatus === "running") {
        stopDryRun();
      }

      onSaved(updated);
      onClose();
    } catch (err) {
      setSaveError(err.message || "Failed to save");
    } finally {
      setSaving(false);
    }
  }, [selected, ignoreText, project, syncAfterSave, dryRunStatus, stopDryRun, onSaved, onClose]);

  if (!open) return null;

  const entryIcon = (entry) => {
    if (entry.type === "symlink") return "🔗";
    if (entry.name === ".") return "📄";
    return "📁";
  };

  const entryLabel = (entry) => {
    if (entry.name === ".") return "Files in project root";
    return entry.name;
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-surface rounded-2xl p-6 max-w-md w-full shadow-card max-h-[85vh] flex flex-col space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-bold text-heading">Select Folders</h3>
          {remoteRoot && (
            <span className="text-xs text-dim font-mono truncate ml-2">{remoteRoot}</span>
          )}
        </div>

        {loading ? (
          <p className="text-sm text-dim py-4 text-center">Loading folders...</p>
        ) : (
          <>
            {/* Select all / Deselect all bar */}
            <div className="grid grid-cols-3 items-center">
              <button
                type="button"
                onClick={handleSelectAll}
                className="justify-self-start text-sm font-medium text-accent hover:opacity-80 transition-colors px-2 py-1"
              >
                {allSelected ? "Deselect all" : "Select all"}
              </button>
              <span className="justify-self-center text-sm text-label">
                {selected.size} of {selectableEntries.length} selected
              </span>
              <span className="justify-self-end" />
            </div>

            {/* Folder list */}
            <div className="overflow-y-auto flex-1 -mx-2 px-2 space-y-1">
              {entries.map((entry) => {
                const isDisabled = entry.default_ignored || entry.type === "symlink";
                const isChecked = selected.has(entry.name);
                const stats = dryRunStats[entry.name];
                const entryBytes = stats?.bytes || 0;
                const isLarge = entryBytes > LARGE_BYTES;

                return (
                  <label
                    key={entry.name}
                    className={`flex items-center gap-3 px-2 py-2 rounded-lg transition-colors ${
                      isDisabled
                        ? "opacity-60 cursor-not-allowed"
                        : "cursor-pointer hover:bg-input"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={isChecked}
                      disabled={isDisabled}
                      onChange={() => !isDisabled && toggleOne(entry.name)}
                      className="w-4 h-4 rounded form-accent cursor-pointer disabled:cursor-not-allowed"
                    />
                    <span className="text-sm flex-1 min-w-0 flex items-center gap-2">
                      <span>{entryIcon(entry)}</span>
                      <span className={`truncate ${isDisabled ? "text-dim" : "text-body"}`}>
                        {entryLabel(entry)}
                      </span>
                      {isDisabled && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-elevated text-dim">
                          ignored
                        </span>
                      )}
                      {isLarge && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-600 dark:text-amber-400">
                          large
                        </span>
                      )}
                    </span>
                    <span className="text-xs text-dim shrink-0 font-mono">
                      {stats
                        ? `${(stats.files || 0).toLocaleString()} files · ${formatBytes(stats.bytes || 0)}`
                        : dryRunStatus === "running"
                          ? "…"
                          : ""}
                    </span>
                  </label>
                );
              })}
            </div>

            {/* Collapsible ignore rules */}
            <div>
              <button
                type="button"
                onClick={() => setShowIgnore(!showIgnore)}
                className="text-xs text-accent hover:opacity-80 transition-colors"
              >
                {showIgnore ? "Hide" : "Extra ignore rules (gitignore syntax)"}
              </button>
              {showIgnore && (
                <textarea
                  value={ignoreText}
                  onChange={(e) => setIgnoreText(e.target.value)}
                  rows={3}
                  className="mt-2 w-full px-3 py-2 text-xs rounded-lg bg-input text-body border border-divider font-mono placeholder-hint resize-y"
                  placeholder={"# one pattern per line\n*.log\ntmp/"}
                />
              )}
            </div>

            {/* Footer: totals + warnings */}
            <div className="pt-2 border-t border-divider space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-dim">
                  {selectedTotals.files.toLocaleString()} files{" "}
                  <span className="text-faint">·</span>{" "}
                  {formatBytes(selectedTotals.bytes)}
                </span>
                {dryRunStatus === "running" && (
                  <span className="text-dim">Scanning...</span>
                )}
                {dryRunError && (
                  <span className="text-danger truncate ml-2">{dryRunError}</span>
                )}
              </div>
              {selectedTotals.files > BUDGET_FILES && (
                <p className="text-xs text-danger">
                  Warning: {selectedTotals.files.toLocaleString()} files exceeds the {BUDGET_FILES.toLocaleString()} file budget
                </p>
              )}

              {saveError && <p className="text-xs text-danger">{saveError}</p>}
              {syncNote && <p className="text-xs text-dim">{syncNote}</p>}

              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={handleClose}
                  className="flex-1 min-h-[44px] rounded-lg bg-input hover:bg-elevated text-body text-sm transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={saving || selected.size === 0}
                  onClick={handleSave}
                  className="flex-1 min-h-[44px] rounded-lg bg-accent hover:opacity-90 text-accent-ink font-semibold text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {saving ? "Saving..." : "Save"}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
