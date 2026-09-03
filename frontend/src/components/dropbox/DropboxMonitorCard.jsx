import { useState, useEffect, useCallback, useRef } from "react";
import {
  fetchDropboxStatus,
  updateDropboxConfig,
  triggerDropboxSync,
  pauseDropboxSync,
  resumeDropboxSync,
  unlinkDropbox,
} from "../../lib/api";
import { useWsEvent } from "../../hooks/useWebSocket";
import usePageVisible from "../../hooks/usePageVisible";
import { formatBytes, formatRelative } from "./format";
import DropboxLinkModal from "./DropboxLinkModal";

export default function DropboxMonitorCard() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [errorsOpen, setErrorsOpen] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const visible = usePageVisible();
  const timerRef = useRef(null);

  const loadStatus = useCallback(async () => {
    try {
      const data = await fetchDropboxStatus();
      setStatus(data);
      setError(null);
    } catch (e) {
      setError(e.message || "Failed to load status");
    }
  }, []);

  // Initial load
  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  // Handle return from Dropbox redirect (dropbox=linked|error in query params)
  const returnHandled = useRef(false);
  useEffect(() => {
    if (returnHandled.current) return;
    const params = new URLSearchParams(window.location.search);
    const dbx = params.get("dropbox");
    if (!dbx) return;
    returnHandled.current = true;

    // Capture message before stripping
    const dbxMessage = params.get("dropbox_message") || "Dropbox authorization failed";

    // Strip dropbox params from URL
    params.delete("dropbox");
    params.delete("dropbox_message");
    const qs = params.toString();
    const clean = window.location.pathname + (qs ? `?${qs}` : "");
    window.history.replaceState(null, "", clean);

    if (dbx === "linked") {
      loadStatus();
    } else if (dbx === "error") {
      setError(dbxMessage);
    }
  }, [loadStatus]);

  // Visibility-gated polling: 3s while current_run, else 30s
  useEffect(() => {
    if (!visible) {
      clearInterval(timerRef.current);
      timerRef.current = null;
      return;
    }
    const interval = status?.current_run ? 3000 : 30000;
    timerRef.current = setInterval(loadStatus, interval);
    return () => clearInterval(timerRef.current);
  }, [visible, !!status?.current_run, loadStatus]);

  // Refetch on dropbox_update WS events
  useWsEvent(useCallback((event) => {
    if (event.type === "dropbox_update") {
      loadStatus();
    }
  }, [loadStatus]));

  // Config update handler (immediate, like backup panel)
  const handleUpdateConfig = useCallback(async (updates) => {
    try {
      await updateDropboxConfig(updates);
      await loadStatus();
    } catch (e) {
      setError(e.message || "Config update failed");
    }
  }, [loadStatus]);

  const handleSyncNow = useCallback(async () => {
    setBusy(true);
    try {
      await triggerDropboxSync();
      await loadStatus();
    } catch (e) {
      setError(e.message || "Sync failed");
    } finally {
      setBusy(false);
    }
  }, [loadStatus]);

  const handlePauseResume = useCallback(async () => {
    setBusy(true);
    try {
      if (status?.config?.paused) {
        await resumeDropboxSync();
      } else {
        await pauseDropboxSync();
      }
      await loadStatus();
    } catch (e) {
      setError(e.message || "Operation failed");
    } finally {
      setBusy(false);
    }
  }, [status?.config?.paused, loadStatus]);

  const handleUnlink = useCallback(async () => {
    if (!window.confirm("Unlink Dropbox? This revokes access and stops all syncing.")) return;
    setBusy(true);
    try {
      await unlinkDropbox();
      await loadStatus();
    } catch (e) {
      setError(e.message || "Unlink failed");
    } finally {
      setBusy(false);
    }
  }, [loadStatus]);

  const handleLinked = useCallback(async () => {
    setLinkOpen(false);
    await loadStatus();
  }, [loadStatus]);

  // Not loaded yet
  if (!status && !error) return null;

  const linked = status?.linked;
  const config = status?.config;
  const currentRun = status?.current_run;
  const lastRun = status?.last_run;
  const projects = status?.projects || [];
  const recentErrors = status?.recent_errors || [];
  const space = status?.space;
  const account = status?.account;

  return (
    <section>
      <h2 className="text-xs font-semibold text-dim uppercase tracking-wider mb-2">Dropbox</h2>
      <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
        {error && (
          <p className="text-xs text-danger">{error}</p>
        )}

        {!linked ? (
          /* Not linked state */
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm text-body">Not linked</p>
              <p className="text-xs text-dim">Enable Dropbox Sync in a project's settings to connect.</p>
            </div>
            <button
              type="button"
              onClick={() => setLinkOpen(true)}
              className="shrink-0 px-3 py-1 rounded-full text-xs font-medium transition-colors accent-tint-15 text-accent hover:accent-tint-25"
            >
              Connect
            </button>
          </div>
        ) : (
          <>
            {/* Account line */}
            <div className="flex items-center justify-between">
              <div className="min-w-0">
                <p className="text-sm text-heading font-medium">
                  {account?.name}
                  <span className="text-dim font-normal"> · {account?.email}</span>
                </p>
                {status?.next_run_at && (
                  <p className="text-xs text-dim mt-0.5">
                    Next sync: {formatRelative(status.next_run_at)}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => setConfigOpen(!configOpen)}
                  title="Settings"
                  className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-input transition-colors"
                >
                  <svg className="w-3.5 h-3.5 text-label" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                  </svg>
                </button>
              </div>
            </div>

            {/* Space usage bar */}
            {space && space.allocated > 0 && (
              <div>
                <div className="flex items-center justify-between text-[10px] text-dim mb-1">
                  <span>{formatBytes(space.used)} used</span>
                  <span>{formatBytes(space.allocated)} total</span>
                </div>
                <div className="h-1.5 rounded-full bg-elevated">
                  <div
                    className="h-full rounded-full bg-accent"
                    style={{ width: `${Math.min(100, (space.used / space.allocated) * 100)}%` }}
                  />
                </div>
              </div>
            )}

            {/* Current run progress or last run summary */}
            {currentRun ? (
              <div className="pt-2 border-t border-divider">
                <div className="flex items-center justify-between">
                  <p className="text-xs text-body">
                    Syncing{currentRun.project ? ` ${currentRun.project}` : ""}
                    {currentRun.phase ? ` (${currentRun.phase})` : ""}
                  </p>
                  <span className="text-[10px] text-dim">
                    {currentRun.files_done}/{currentRun.files_total} files
                  </span>
                </div>
                {currentRun.files_total > 0 && (
                  <div className="h-1.5 rounded-full bg-elevated mt-1">
                    <div
                      className="h-full rounded-full bg-accent transition-all"
                      style={{ width: `${Math.min(100, (currentRun.files_done / currentRun.files_total) * 100)}%` }}
                    />
                  </div>
                )}
                {currentRun.errors > 0 && (
                  <p className="text-[10px] text-danger mt-0.5">{currentRun.errors} errors</p>
                )}
              </div>
            ) : lastRun ? (
              <div className="pt-2 border-t border-divider">
                <div className="flex items-center justify-between">
                  <p className="text-xs text-body">
                    Last run: {lastRun.status}
                    {lastRun.files_uploaded > 0 && ` · ${lastRun.files_uploaded} files`}
                    {lastRun.bytes_uploaded > 0 && ` · ${formatBytes(lastRun.bytes_uploaded)}`}
                    {lastRun.errors > 0 && ` · ${lastRun.errors} errors`}
                  </p>
                  <span className="text-[10px] text-dim">
                    {lastRun.finished_at ? formatRelative(lastRun.finished_at) : ""}
                  </span>
                </div>
              </div>
            ) : null}

            {/* Config panel (collapsible) */}
            {configOpen && (
              <div className="pt-2 border-t border-divider space-y-3">
                {/* Interval */}
                <div className="flex items-center justify-between">
                  <span className="text-xs text-label">Interval</span>
                  <select
                    value={config?.interval_hours ?? 1}
                    onChange={(e) => handleUpdateConfig({ interval_hours: parseInt(e.target.value) })}
                    className="text-xs bg-input text-heading rounded-lg px-2 py-1 border border-divider"
                  >
                    <option value={1}>1h</option>
                    <option value={3}>3h</option>
                    <option value={6}>6h</option>
                    <option value={12}>12h</option>
                    <option value={24}>24h</option>
                  </select>
                </div>
                {/* Concurrency */}
                <div className="flex items-center justify-between">
                  <span className="text-xs text-label">Concurrency</span>
                  <select
                    value={config?.concurrency ?? 4}
                    onChange={(e) => handleUpdateConfig({ concurrency: parseInt(e.target.value) })}
                    className="text-xs bg-input text-heading rounded-lg px-2 py-1 border border-divider"
                  >
                    <option value={1}>1</option>
                    <option value={2}>2</option>
                    <option value={4}>4</option>
                    <option value={8}>8</option>
                  </select>
                </div>
                {/* Max file size */}
                <div className="flex items-center justify-between">
                  <span className="text-xs text-label">Max file size</span>
                  <select
                    value={config?.max_file_mb ?? 2048}
                    onChange={(e) => handleUpdateConfig({ max_file_mb: parseInt(e.target.value) })}
                    className="text-xs bg-input text-heading rounded-lg px-2 py-1 border border-divider"
                  >
                    <option value={256}>256 MB</option>
                    <option value={1024}>1 GB</option>
                    <option value={2048}>2 GB</option>
                    <option value={8192}>8 GB</option>
                  </select>
                </div>
                {/* Bandwidth */}
                <div className="flex items-center justify-between">
                  <span className="text-xs text-label">Bandwidth</span>
                  <select
                    value={config?.bandwidth_kbps ?? 0}
                    onChange={(e) => handleUpdateConfig({ bandwidth_kbps: parseInt(e.target.value) })}
                    className="text-xs bg-input text-heading rounded-lg px-2 py-1 border border-divider"
                  >
                    <option value={0}>Unlimited</option>
                    <option value={1000}>1 Mbps</option>
                    <option value={5000}>5 Mbps</option>
                    <option value={20000}>20 Mbps</option>
                  </select>
                </div>
                {/* Prune toggle */}
                <div className="flex items-center justify-between">
                  <span className="text-xs text-label">Prune deleted files</span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={config?.prune ? "true" : "false"}
                    onClick={() => handleUpdateConfig({ prune: !config?.prune })}
                    className={`relative w-9 h-5 rounded-full transition-colors ${config?.prune ? "bg-accent" : "bg-zinc-600"}`}
                  >
                    <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${config?.prune ? "left-[18px]" : "left-0.5"}`} />
                  </button>
                </div>
                {/* Allowlist toggle */}
                <div className="flex items-center justify-between">
                  <span className="text-xs text-label">Allowlist mode</span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={config?.allowlist_mode ? "true" : "false"}
                    onClick={() => handleUpdateConfig({ allowlist_mode: !config?.allowlist_mode })}
                    className={`relative w-9 h-5 rounded-full transition-colors ${config?.allowlist_mode ? "bg-accent" : "bg-zinc-600"}`}
                  >
                    <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${config?.allowlist_mode ? "left-[18px]" : "left-0.5"}`} />
                  </button>
                </div>
              </div>
            )}

            {/* Per-project table */}
            {projects.length > 0 && (
              <div className="pt-2 border-t border-divider">
                <div className="space-y-1 max-h-60 overflow-y-auto">
                  {projects.map((p) => (
                    <div key={p.name} className="flex items-center gap-2 py-1 px-1 rounded hover:bg-elevated/50 text-xs">
                      <span
                        className={`shrink-0 w-2 h-2 rounded-full ${p.enabled ? "text-ok bg-current" : "text-dim bg-current"}`}
                        title={p.enabled ? "Enabled" : "Disabled"}
                      />
                      <span className="text-heading font-medium truncate min-w-0 flex-1" title={p.name}>
                        {p.display_name || p.name}
                      </span>
                      <span className="text-dim shrink-0">
                        {p.files_synced != null ? `${p.files_synced} files` : ""}
                      </span>
                      <span className="text-dim shrink-0">
                        {p.bytes_synced != null && p.bytes_synced > 0 ? formatBytes(p.bytes_synced) : ""}
                      </span>
                      <span className="text-dim shrink-0">
                        {p.last_synced_at ? formatRelative(p.last_synced_at) : "never"}
                      </span>
                      {p.last_error && (
                        <span className="text-danger shrink-0 truncate max-w-[120px]" title={p.last_error}>
                          {p.last_error}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Recent errors (collapsible) */}
            {recentErrors.length > 0 && (
              <div className="pt-2 border-t border-divider">
                <button
                  type="button"
                  onClick={() => setErrorsOpen(!errorsOpen)}
                  className="text-[11px] font-medium text-danger hover:opacity-80 transition-colors"
                >
                  {errorsOpen ? "Hide" : "Show"} {recentErrors.length} recent error{recentErrors.length !== 1 ? "s" : ""}
                </button>
                {errorsOpen && (
                  <div className="mt-1.5 space-y-1 max-h-40 overflow-y-auto">
                    {recentErrors.map((err, i) => (
                      <div key={i} className="text-[10px] text-dim py-0.5">
                        <span className="text-faint">{err.at ? formatRelative(err.at) : ""}</span>
                        {err.project && <span className="text-label"> {err.project}</span>}
                        {err.path && <span className="text-faint"> {err.path}</span>}
                        {" "}<span className="text-danger">{err.message}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Action buttons row */}
            <div className="flex flex-wrap items-center gap-1.5 pt-2 border-t border-divider">
              <button
                type="button"
                disabled={busy || config?.paused}
                onClick={handleSyncNow}
                className="px-2 py-0.5 rounded text-[11px] font-medium accent-tint-20 text-accent hover:accent-tint-25 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {busy ? "..." : config?.paused ? "Paused" : "Sync now"}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={handlePauseResume}
                className="px-2 py-0.5 rounded text-[11px] font-medium bg-amber-500/15 text-amber-600 hover:bg-amber-500/25 dark:bg-amber-500/10 dark:text-amber-400 dark:hover:bg-amber-500/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {config?.paused ? "Resume" : "Pause"}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={handleUnlink}
                className="ml-auto px-2 py-0.5 rounded text-[11px] font-medium danger-tint-20 text-danger hover:danger-tint-25 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Unlink
              </button>
            </div>
          </>
        )}
      </div>

      {/* Link modal */}
      {linkOpen && (
        <DropboxLinkModal
          open={linkOpen}
          appKey={status?.app_key || ""}
          linkMode={status?.link_mode}
          returnTo="/monitor"
          onClose={() => setLinkOpen(false)}
          onLinked={handleLinked}
        />
      )}
    </section>
  );
}
