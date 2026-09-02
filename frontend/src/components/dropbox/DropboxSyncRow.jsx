import { useState, useEffect, useCallback, useRef } from "react";
import {
  fetchProjectDropboxStatus,
  updateProjectSettings,
  triggerDropboxSync,
} from "../../lib/api";
import { useWsEvent } from "../../hooks/useWebSocket";
import usePageVisible from "../../hooks/usePageVisible";
import { formatBytes, formatRelative } from "./format";
import DropboxLinkModal from "./DropboxLinkModal";
import DropboxFolderPicker from "./DropboxFolderPicker";

/**
 * Settings row for Dropbox Sync on ProjectDetailPage.
 * Polls per-project dropbox status, shows toggle + inline controls.
 */
export default function DropboxSyncRow({ project, onProjectChange }) {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showLink, setShowLink] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [pickerSyncAfterSave, setPickerSyncAfterSave] = useState(true);
  const [syncBusy, setSyncBusy] = useState(false);

  const visible = usePageVisible();
  const pollRef = useRef(null);
  const mountedRef = useRef(true);

  const name = project?.name;
  const isOn = !!project?.dropbox_sync;

  // Fetch status
  const fetchStatus = useCallback(async () => {
    if (!name) return;
    try {
      const s = await fetchProjectDropboxStatus(name);
      if (mountedRef.current) setStatus(s);
    } catch {
      // silent
    }
  }, [name]);

  // Mount / unmount
  useEffect(() => {
    mountedRef.current = true;
    fetchStatus();
    return () => { mountedRef.current = false; };
  }, [fetchStatus]);

  // WS events: refetch on dropbox_update or project_update for this project
  useWsEvent(
    useCallback(
      (event) => {
        if (event.type === "dropbox_update" || (event.type === "project_update" && event.data?.name === name)) {
          fetchStatus();
        }
      },
      [name, fetchStatus],
    ),
  );

  // Poll every 3s only when run_active && (current || queued) AND page visible
  useEffect(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }

    const shouldPoll =
      visible && status?.run_active && (status?.current || status?.queued);

    if (shouldPoll) {
      pollRef.current = setInterval(fetchStatus, 3000);
    }

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [visible, status?.run_active, status?.current, status?.queued, fetchStatus]);

  // Toggle handler
  const handleToggle = useCallback(async () => {
    if (busy) return;
    setError("");

    if (isOn) {
      // On -> Off: optimistic, revert on failure
      onProjectChange({ dropbox_sync: false });
      setBusy(true);
      try {
        const updated = await updateProjectSettings(name, { dropbox_sync: false });
        onProjectChange(updated);
      } catch (err) {
        onProjectChange({ dropbox_sync: true }); // revert
        setError(err.message || "Failed to disable sync");
      } finally {
        setBusy(false);
      }
    } else {
      // Off -> On: open link modal or picker (never flip toggle until save)
      if (!status?.linked) {
        setShowLink(true);
      } else {
        setPickerSyncAfterSave(true);
        setShowPicker(true);
      }
    }
  }, [busy, isOn, name, status?.linked, onProjectChange]);

  // After linking, open the picker
  const handleLinked = useCallback(() => {
    setShowLink(false);
    fetchStatus();
    setPickerSyncAfterSave(true);
    setShowPicker(true);
  }, [fetchStatus]);

  // Picker saved
  const handlePickerSaved = useCallback(
    (updated) => {
      onProjectChange(updated);
      fetchStatus();
    },
    [onProjectChange, fetchStatus],
  );

  // Open picker in "Folders..." mode (no sync after save)
  const handleOpenFolders = useCallback(() => {
    setPickerSyncAfterSave(false);
    setShowPicker(true);
  }, []);

  // Sync now
  const handleSyncNow = useCallback(async () => {
    setSyncBusy(true);
    try {
      await triggerDropboxSync(name);
      fetchStatus();
    } catch {
      // silent
    } finally {
      setSyncBusy(false);
    }
  }, [name, fetchStatus]);

  // Build subtitle
  const subtitle = (() => {
    if (!status) return "Loading...";

    // Syncing progress
    if (isOn && status.current) {
      const done = status.current.files_done || 0;
      const total = status.current.files_total || 0;
      return `Syncing... ${done}/${total} files`;
    }

    if (!status.linked) return "Off — back up this project to Dropbox";
    if (!isOn) return `Off — linked as ${status.account_email || "unknown"}`;

    // On: build stats line
    const parts = [];
    const folders = status.folders;
    const folderTotal = status.folder_total;
    if (folders && folderTotal) {
      parts.push(`${folders.length} of ${folderTotal} folders`);
    }
    if (status.files_synced != null) {
      parts.push(`${status.files_synced.toLocaleString()} files`);
    }
    if (status.bytes_synced != null) {
      parts.push(formatBytes(status.bytes_synced));
    }
    const lastSync = formatRelative(status.last_synced_at);
    parts.push(`last sync ${lastSync}`);
    return parts.join(" · ");
  })();

  // Progress bar
  const showProgress = isOn && status?.current;
  const progressPct = showProgress
    ? status.current.files_total > 0
      ? Math.round((status.current.files_done / status.current.files_total) * 100)
      : 0
    : 0;

  // Project error from status
  const lastError = status?.last_error;

  const isPaused = status?.paused;
  const isRunning = !!status?.current || !!status?.queued;

  return (
    <>
      {/* Main settings row */}
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm text-body">Dropbox Sync</p>
            <p className="text-xs text-dim">{subtitle}</p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={isOn ? "true" : "false"}
            disabled={busy}
            onClick={handleToggle}
            className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
              isOn ? "bg-accent" : "bg-elevated"
            }`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                isOn ? "translate-x-6" : "translate-x-1"
              }`}
            />
          </button>
        </div>

        {/* Progress bar */}
        {showProgress && (
          <div className="h-1.5 rounded-full bg-elevated">
            <div
              className="h-full rounded-full bg-accent transition-all"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        )}

        {/* Error line */}
        {error && <p className="text-xs text-danger">{error}</p>}
        {!error && lastError && <p className="text-xs text-danger">{lastError}</p>}

        {/* Action buttons (only when on) */}
        {isOn && (
          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              onClick={handleOpenFolders}
              className="shrink-0 px-3 py-1 rounded-full text-xs font-medium transition-colors accent-tint-15 text-accent hover:accent-tint-25"
            >
              Folders...
            </button>
            <button
              type="button"
              disabled={syncBusy || isRunning || isPaused}
              onClick={handleSyncNow}
              className="shrink-0 px-3 py-1 rounded-full text-xs font-medium transition-colors accent-tint-15 text-accent hover:accent-tint-25 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isPaused ? "Paused" : syncBusy || isRunning ? "Syncing..." : "Sync now"}
            </button>
          </div>
        )}
      </div>

      {/* Link modal */}
      <DropboxLinkModal
        open={showLink}
        initialAppKey={status?.app_key || ""}
        onClose={() => setShowLink(false)}
        onLinked={handleLinked}
      />

      {/* Folder picker */}
      <DropboxFolderPicker
        open={showPicker}
        project={name}
        remoteRoot={`/${name}`}
        initialFolders={project?.dropbox_folders ? JSON.parse(project.dropbox_folders) : null}
        initialIgnore={project?.dropbox_ignore || ""}
        onClose={() => setShowPicker(false)}
        onSaved={handlePickerSaved}
        syncAfterSave={pickerSyncAfterSave}
      />
    </>
  );
}
