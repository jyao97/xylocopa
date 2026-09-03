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

  const enabledProjects = projects.filter((p) => p.enabled);
  const disabledCount = projects.length - enabledProjects.length;

  // Sort enabled projects: last_synced_at desc, never-synced last
  const sortedProjects = [...enabledProjects].sort((a, b) => {
    if (!a.last_synced_at && !b.last_synced_at) return 0;
    if (!a.last_synced_at) return 1;
    if (!b.last_synced_at) return -1;
    return new Date(b.last_synced_at) - new Date(a.last_synced_at);
  });

  const enabledCount = enabledProjects.length;
  const pluralS = enabledCount === 1 ? "" : "s";

  // Space usage percent
  const spacePct = space && space.allocated > 0
    ? Math.min(100, Math.round((space.used / space.allocated) * 100))
    : 0;
  const spaceBarColor = spacePct >= 90 ? "bg-danger" : spacePct >= 70 ? "bg-attn" : "bg-accent";

  // Build subtitle text
  let subtitle = "";
  if (linked && config) {
    if (config.paused) {
      subtitle = `Paused · ${enabledCount} project${pluralS}`;
    } else {
      subtitle = `Auto: every ${config.interval_hours ?? 1}h · ${enabledCount} project${pluralS}`;
      if (status?.next_run_at) {
        subtitle += ` · next ${formatRelative(status.next_run_at)}`;
      }
    }
  }

  // Status line helpers
  const renderStatusLine = () => {
    if (currentRun) {
      const pct = currentRun.files_total > 0
        ? Math.min(100, Math.round((currentRun.files_done / currentRun.files_total) * 100))
        : 0;
      const progressBarColor = pct >= 90 ? "bg-danger" : pct >= 70 ? "bg-attn" : "bg-accent";
      return (
        <div className="pt-2 border-t border-divider space-y-1">
          <div className="flex items-center gap-2">
            <span className="inline-block w-2.5 h-2.5 rounded-full bg-accent animate-pulse shrink-0" />
            <span className="text-xs text-heading min-w-0 truncate">
              Syncing {currentRun.project ? currentRun.project : ""}
              {" "}<span className="font-mono">{currentRun.files_done}/{currentRun.files_total}</span> files
            </span>
          </div>
          {currentRun.files_total > 0 && (
            <div className="h-2 rounded-full bg-elevated overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${progressBarColor}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          )}
          {currentRun.errors > 0 && (
            <p className="text-xs text-danger"><span className="font-mono">{currentRun.errors}</span> errors</p>
          )}
        </div>
      );
    }

    if (lastRun) {
      const statusWord = (lastRun.status || "").charAt(0).toUpperCase() + (lastRun.status || "").slice(1);
      if (lastRun.status === "ok") {
        const parts = [];
        if (lastRun.files_uploaded > 0) parts.push(`${lastRun.files_uploaded} files`);
        if (lastRun.bytes_uploaded > 0) parts.push(formatBytes(lastRun.bytes_uploaded));
        return (
          <div className="pt-2 border-t border-divider">
            <div className="flex items-center gap-2">
              <span className="inline-block w-2.5 h-2.5 rounded-full bg-ok shrink-0" />
              <span className="text-xs text-heading min-w-0 truncate">
                Synced {lastRun.finished_at ? formatRelative(lastRun.finished_at) : ""}
              </span>
              {parts.length > 0 && (
                <span className="text-xs text-dim font-mono shrink-0 ml-auto">
                  {parts.join(" · ")}
                </span>
              )}
            </div>
          </div>
        );
      }
      // error/cancelled/interrupted
      const dotColor = (lastRun.status === "cancelled" || lastRun.status === "interrupted") ? "bg-attn" : "bg-danger";
      return (
        <div className="pt-2 border-t border-divider">
          <div className="flex items-center gap-2">
            <span className={`inline-block w-2.5 h-2.5 rounded-full ${dotColor} shrink-0`} />
            <span className="text-xs text-heading min-w-0 truncate">
              {statusWord} {lastRun.finished_at ? formatRelative(lastRun.finished_at) : ""}
            </span>
          </div>
          {lastRun.error_sample && (
            <p className="text-[10px] text-danger truncate ml-[18px]" title={lastRun.error_sample}>
              {lastRun.error_sample}
            </p>
          )}
        </div>
      );
    }

    return (
      <div className="pt-2 border-t border-divider">
        <div className="flex items-center gap-2">
          <span className="inline-block w-2.5 h-2.5 rounded-full bg-elevated shrink-0" />
          <span className="text-xs text-heading">Not synced yet</span>
        </div>
      </div>
    );
  };

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
              <p className="text-sm text-heading font-medium">Not linked</p>
              <p className="text-xs text-dim mt-0.5">Enable Dropbox Sync in a project's settings to connect.</p>
            </div>
            <button
              type="button"
              onClick={() => setLinkOpen(true)}
              className="shrink-0 px-2 py-0.5 rounded text-[11px] font-medium accent-tint-20 text-accent hover:accent-tint-25 transition-colors"
            >
              Connect
            </button>
          </div>
        ) : (
          <>
            {/* Header row: account + subtitle */}
            <div className="flex items-center justify-between">
              <div className="min-w-0">
                <p className="text-sm text-heading font-medium">
                  {account?.name}
                  <span className="text-dim font-normal"> &middot; {account?.email}</span>
                </p>
                {subtitle && (
                  <p className="text-xs text-dim mt-0.5">{subtitle}</p>
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

            {/* Space usage bar (UsageBar idiom) */}
            {space && space.allocated > 0 && (
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-label">Dropbox space</span>
                  <span className="text-xs text-dim font-mono">{formatBytes(space.used)} / {formatBytes(space.allocated)}</span>
                </div>
                <div className="h-2 rounded-full bg-elevated overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${spaceBarColor}`}
                    style={{ width: `${spacePct}%` }}
                  />
                </div>
              </div>
            )}

            {/* Status line (HealthCard-style dot + text) */}
            {renderStatusLine()}

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

            {/* Per-project list (enabled only, sorted) */}
            <div className="pt-2 border-t border-divider">
              {sortedProjects.length > 0 ? (
                <div className="space-y-1 max-h-60 overflow-y-auto">
                  {sortedProjects.map((p) => {
                    const isSyncing = currentRun?.project === p.name;
                    const dotColor = isSyncing
                      ? "bg-accent animate-pulse"
                      : p.last_error
                        ? "bg-danger"
                        : p.last_synced_at
                          ? "bg-ok"
                          : "bg-elevated";
                    const hasStats = p.files_synced != null;
                    const rightParts = [];
                    if (hasStats) {
                      rightParts.push(`${p.files_synced} files`);
                      if (p.bytes_synced > 0) rightParts.push(formatBytes(p.bytes_synced));
                    }
                    const rightText = rightParts.length > 0 ? rightParts.join(" · ") : "—";
                    return (
                      <div key={p.name} className="flex items-center justify-between py-1.5 px-2 rounded-lg hover:bg-elevated/50">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className={`shrink-0 w-2 h-2 rounded-full ${dotColor}`} />
                          <div className="min-w-0">
                            <p className="text-xs text-heading font-medium truncate" title={p.name}>
                              {p.display_name || p.name}
                            </p>
                            {p.last_error ? (
                              <p className="text-[10px] text-danger truncate" title={p.last_error}>
                                {p.last_error}
                              </p>
                            ) : (
                              <p className="text-[10px] text-faint">
                                {p.last_synced_at ? formatRelative(p.last_synced_at) : "never"}
                              </p>
                            )}
                          </div>
                        </div>
                        <span className="text-xs text-dim font-mono shrink-0 ml-2">
                          {rightText}
                        </span>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="text-[10px] text-faint">
                  {disabledCount > 0
                    ? `${disabledCount} project${disabledCount === 1 ? "" : "s"} not syncing — enable Dropbox Sync in a project’s settings`
                    : "No projects configured"}
                </p>
              )}
              {sortedProjects.length > 0 && disabledCount > 0 && (
                <p className="text-[10px] text-faint mt-1.5">
                  {disabledCount} project{disabledCount === 1 ? "" : "s"} not syncing &mdash; enable Dropbox Sync in a project&rsquo;s settings
                </p>
              )}
            </div>

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
                      <div key={i} className="text-[10px] py-0.5">
                        <span className="font-mono text-faint">{err.at ? formatRelative(err.at) : ""}</span>
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
