import { useEffect, useState, useCallback } from "react";
import PageHeader from "../components/PageHeader";
import {
  scanOrphans, cleanOrphans, fetchBackupStatus, purgeBackups,
  triggerBackup, deleteSingleBackup, restoreBackup, updateBackupConfig,
  importBackup, downloadBackupUrl, truncateLogs,
  fetchTelemetryStatus, setTelemetryEnabled,
} from "../lib/api";
import { useMonitor } from "../contexts/MonitorContext";
import { getEinkMode, setEinkMode } from "../lib/einkMode";

const HEALTH_COLORS = {
  ok: "bg-green-500",
  error: "bg-red-500",
  degraded: "bg-yellow-500",
  unavailable: "bg-red-500",
  unknown: "bg-gray-500",
};


function formatResetTime(isoStr) {
  if (!isoStr) return "";
  const d = new Date(isoStr);
  if (isNaN(d)) return "";
  const now = new Date();
  const diffMs = d - now;
  if (diffMs <= 0) return "now";
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 60) return `in ${diffMin}m`;
  const diffH = Math.floor(diffMin / 60);
  const remMin = diffMin % 60;
  if (diffH < 24) return `in ${diffH}h${remMin > 0 ? ` ${remMin}m` : ""}`;
  const diffD = Math.floor(diffH / 24);
  const remH = diffH % 24;
  return `in ${diffD}d ${remH}h`;
}

function UsageBar({ label, pct, detail }) {
  const barColor =
    pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-amber-500" : "bg-cyan-500";
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-label">{label}</span>
        <span className="text-xs text-dim font-mono">{detail}</span>
      </div>
      <div className="h-2 rounded-full bg-elevated overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
    </div>
  );
}

function HealthCard({ label, status }) {
  const color = HEALTH_COLORS[status] || HEALTH_COLORS.unknown;
  return (
    <div className="rounded-xl bg-surface shadow-card p-4 flex items-center gap-3 min-w-0">
      <span className={`inline-block w-2.5 h-2.5 rounded-full ${color}`} />
      <div className="min-w-0">
        <p className="text-xs text-dim uppercase tracking-wider">{label}</p>
        <p className="text-sm font-medium text-heading truncate">{status}</p>
      </div>
    </div>
  );
}

const STORAGE_COLORS = {
  cyan: { ring: "stroke-cyan-500", dot: "bg-cyan-500", bar: "bg-cyan-500" },
  violet: { ring: "stroke-violet-500", dot: "bg-violet-500", bar: "bg-violet-500" },
  amber: { ring: "stroke-amber-500", dot: "bg-amber-500", bar: "bg-amber-500" },
  emerald: { ring: "stroke-emerald-500", dot: "bg-emerald-500", bar: "bg-emerald-500" },
  orange: { ring: "stroke-orange-500", dot: "bg-orange-500", bar: "bg-orange-500" },
  rose: { ring: "stroke-rose-500", dot: "bg-rose-500", bar: "bg-rose-500" },
  gray: { ring: "stroke-gray-400", dot: "bg-gray-400", bar: "bg-gray-400" },
};

// Hex values for SVG stroke (Tailwind classes don't work on SVG stroke directly)
const STORAGE_HEX = {
  cyan: "#06b6d4", violet: "#8b5cf6", amber: "#f59e0b",
  emerald: "#10b981", orange: "#f97316", rose: "#f43f5e", gray: "#9ca3af",
};

function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const val = bytes / Math.pow(1024, i);
  return `${val < 10 ? val.toFixed(1) : Math.round(val)} ${units[i]}`;
}

function StorageChart({ data, onRefresh }) {
  const [cleaning, setCleaning] = useState(false);

  const handleCleanLogs = useCallback(async () => {
    setCleaning(true);
    try {
      await truncateLogs();
      if (onRefresh) await onRefresh();
    } catch (err) {
      console.error("Failed to truncate logs:", err);
    } finally {
      setCleaning(false);
    }
  }, [onRefresh]);

  if (!data) return null;
  const { categories, total_bytes } = data;
  const visible = categories.filter((c) => c.size_bytes > 0);
  if (visible.length === 0) return null;

  const radius = 52;
  const stroke = 14;
  const size = 140;
  const circumference = 2 * Math.PI * radius;

  // Build segments
  let offset = 0;
  const segments = visible.map((cat) => {
    const pct = total_bytes > 0 ? cat.size_bytes / total_bytes : 0;
    const dash = pct * circumference;
    const gap = circumference - dash;
    const seg = { ...cat, pct, dash, gap, offset };
    offset += dash;
    return seg;
  });

  return (
    <section>
      <h2 className="text-xs font-semibold text-dim uppercase tracking-wider mb-2">Storage</h2>
      <div className="rounded-xl bg-surface shadow-card p-4 flex items-center gap-4">
        {/* Donut ring — left */}
        <div className="relative shrink-0" style={{ width: size, height: size }}>
          <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
            <circle
              cx={size / 2} cy={size / 2} r={radius}
              fill="none" strokeWidth={stroke}
              className="stroke-elevated"
            />
            {segments.map((seg) => (
              <circle
                key={seg.name}
                cx={size / 2} cy={size / 2} r={radius}
                fill="none" strokeWidth={stroke}
                stroke={STORAGE_HEX[seg.color] || STORAGE_HEX.gray}
                strokeDasharray={`${seg.dash} ${seg.gap}`}
                strokeDashoffset={-seg.offset}
                strokeLinecap="butt"
                transform={`rotate(-90 ${size / 2} ${size / 2})`}
              />
            ))}
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-sm font-bold text-heading">{formatBytes(total_bytes)}</span>
            <span className="text-[10px] text-dim">total</span>
          </div>
        </div>
        {/* Legend — right */}
        <div className="flex-1 min-w-0 space-y-1">
          {visible.map((cat) => {
            const colors = STORAGE_COLORS[cat.color] || STORAGE_COLORS.gray;
            const isLogs = cat.name === "Logs";
            return (
              <div key={cat.name} className="flex items-center gap-2 text-xs">
                <span className={`w-2 h-2 rounded-full shrink-0 ${colors.dot}`} />
                <span className="text-label truncate flex-1">{cat.name}</span>
                <span className="text-dim font-mono shrink-0">{formatBytes(cat.size_bytes)}</span>
                {isLogs && cat.size_bytes > 50 * 1024 * 1024 && (
                  <button
                    onClick={handleCleanLogs}
                    disabled={cleaning}
                    className="ml-1 text-dim hover:text-orange-500 transition-colors disabled:opacity-40"
                    title="Truncate log files"
                  >
                    {cleaning ? (
                      <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83" /></svg>
                    ) : (
                      <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" /></svg>
                    )}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function TokenUsageSection({ tokenUsage, onRefresh }) {
  const [spinning, setSpinning] = useState(false);
  const handleRefresh = useCallback(async () => {
    setSpinning(true);
    await onRefresh();
    setTimeout(() => setSpinning(false), 400);
  }, [onRefresh]);

  return (
    <section>
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-xs font-semibold text-dim uppercase tracking-wider">Token Usage</h2>
        <button
          type="button"
          onClick={handleRefresh}
          title="Refresh token usage"
          className="w-6 h-6 flex items-center justify-center rounded-md hover:bg-input transition-colors"
        >
          <svg className={`w-3.5 h-3.5 text-dim ${spinning ? "animate-spin-reverse" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        </button>
      </div>
      <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
        {!tokenUsage || tokenUsage._error ? (
          <p className="text-xs text-faint">
            {tokenUsage?._error ? "Unable to fetch — tap refresh to retry" : "Loading..."}
          </p>
        ) : (
          <>
            {tokenUsage.session && (
              <UsageBar
                label="Session (5h)"
                pct={tokenUsage.session.utilization ?? 0}
                detail={`${tokenUsage.session.utilization ?? 0}% — resets ${formatResetTime(tokenUsage.session.resets_at)}`}
              />
            )}
            {tokenUsage.weekly && (
              <UsageBar
                label="Weekly (7d)"
                pct={tokenUsage.weekly.utilization ?? 0}
                detail={`${tokenUsage.weekly.utilization ?? 0}% — resets ${formatResetTime(tokenUsage.weekly.resets_at)}`}
              />
            )}
            {(tokenUsage.weekly_scoped || []).map((m) => (
              <UsageBar
                key={m.label}
                label={`Weekly (${m.label})`}
                pct={m.utilization ?? 0}
                detail={`${m.utilization ?? 0}% — resets ${formatResetTime(m.resets_at)}`}
              />
            ))}
          </>
        )}
      </div>
    </section>
  );
}

export default function MonitorPage({ theme, onToggleTheme }) {
  const {
    health, healthError, sysStats, tokenUsage, storageStats,
    refresh, refreshTokenUsage, activate, deactivate,
  } = useMonitor();
  const [refreshing, setRefreshing] = useState(false);
  const [orphanBusy, setOrphanBusy] = useState(false);
  const [orphanResult, setOrphanResult] = useState(null);
  const [backupInfo, setBackupInfo] = useState(null);
  const [backupBusy, setBackupBusy] = useState(false);
  const [backupListOpen, setBackupListOpen] = useState(false);
  const [backupConfigOpen, setBackupConfigOpen] = useState(false);
  const [telemetry, setTelemetry] = useState(null);
  const [telemetryBusy, setTelemetryBusy] = useState(false);
  const [einkOn, setEinkOn] = useState(() => getEinkMode());

  const handleEinkToggle = useCallback(() => {
    const next = !einkOn;
    setEinkOn(next);
    setEinkMode(next);
  }, [einkOn]);

  const loadTelemetry = useCallback(async () => {
    try {
      setTelemetry(await fetchTelemetryStatus());
    } catch (e) {
      console.warn("Telemetry status fetch failed:", e);
    }
  }, []);

  const handleTelemetryToggle = useCallback(async () => {
    if (!telemetry || telemetry.env_locked || telemetryBusy) return;
    setTelemetryBusy(true);
    try {
      const next = await setTelemetryEnabled(!telemetry.enabled);
      setTelemetry(next);
    } catch (e) {
      console.warn("Telemetry toggle failed:", e);
    } finally {
      setTelemetryBusy(false);
    }
  }, [telemetry, telemetryBusy]);

  const loadBackupInfo = useCallback(async () => {
    try {
      const info = await fetchBackupStatus();
      setBackupInfo(info);
    } catch (e) {
      console.warn("Backup operation failed:", e);
    }
  }, []);

  // Activate fast polling while this page is mounted; show cached data
  // immediately, then do a fresh fetch.
  useEffect(() => {
    activate();
    refresh();
    loadBackupInfo();
    loadTelemetry();
    return () => deactivate();
  }, [activate, deactivate, refresh, loadBackupInfo, loadTelemetry]);

  const handlePurgeBackups = useCallback(async () => {
    if (!backupInfo || backupInfo.backup_count === 0) return;
    if (!window.confirm(
      `Delete all ${backupInfo.backup_count} backup snapshots (${formatBytes(backupInfo.total_bytes)})?\n\nThis cannot be undone.`
    )) return;
    setBackupBusy(true);
    try {
      await purgeBackups();
      await loadBackupInfo();
      refresh();
    } catch (e) {
      console.warn("Backup operation failed:", e);
    } finally {
      setBackupBusy(false);
    }
  }, [backupInfo, loadBackupInfo, refresh]);

  const handleManualBackup = useCallback(async () => {
    setBackupBusy(true);
    try {
      await triggerBackup();
      await loadBackupInfo();
    } catch (e) {
      console.warn("Backup operation failed:", e);
    } finally {
      setBackupBusy(false);
    }
  }, [loadBackupInfo]);

  const handleDeleteBackup = useCallback(async (name) => {
    if (!window.confirm(`Delete backup "${name}"?`)) return;
    try {
      await deleteSingleBackup(name);
      await loadBackupInfo();
    } catch (e) {
      console.warn("Backup operation failed:", e);
    }
  }, [loadBackupInfo]);

  const handleRestoreBackup = useCallback(async (name) => {
    if (!window.confirm(
      `Restore from "${name}"?\n\nThis will overwrite the current database. The server will need to be restarted.`
    )) return;
    setBackupBusy(true);
    try {
      await restoreBackup(name);
      window.alert("Restore complete. Please restart the server for changes to take effect.");
    } catch (err) {
      window.alert(`Restore failed: ${err.message || "Unknown error"}`);
    } finally {
      setBackupBusy(false);
    }
  }, []);

  const handleImportBackup = useCallback(async () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".zip";
    input.onchange = async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      setBackupBusy(true);
      try {
        await importBackup(file);
        await loadBackupInfo();
      } catch (err) {
        window.alert(`Import failed: ${err.message || "Unknown error"}`);
      } finally {
        setBackupBusy(false);
      }
    };
    input.click();
  }, [loadBackupInfo]);

  const handleUpdateConfig = useCallback(async (updates) => {
    try {
      await updateBackupConfig(updates);
      await loadBackupInfo();
    } catch (e) {
      console.warn("Backup operation failed:", e);
    }
  }, [loadBackupInfo]);

  const handleOrphanClean = useCallback(async () => {
    setOrphanBusy(true);
    setOrphanResult(null);
    try {
      const scan = await scanOrphans();
      const projectCount = scan.orphan_project_count || 0;
      if (scan.total_files === 0 && projectCount === 0) {
        setOrphanResult({ freed_bytes: 0, message: "No orphans found" });
        return;
      }
      const ok = window.confirm(
        `Delete ${scan.total_files} orphaned files (${formatBytes(scan.total_bytes)})?\n\n` +
        `${scan.orphan_session_count} session files (${formatBytes(scan.orphan_session_bytes)})\n` +
        `${scan.orphan_log_count} log files (${formatBytes(scan.orphan_log_bytes)})\n` +
        `${scan.empty_dir_count} empty directories\n` +
        `${projectCount} ghost projects (folder missing)`
      );
      if (!ok) return;
      const result = await cleanOrphans();
      setOrphanResult(result);
      refresh();
    } catch (err) {
      setOrphanResult({ error: err.message || "Failed" });
    } finally {
      setOrphanBusy(false);
    }
  }, [refresh]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await refresh();
    // Minimum 400ms spinner display to prevent jarring sub-frame flicker
    setTimeout(() => setRefreshing(false), 400);
  }, [refresh]);

  return (
    <div className="h-full flex flex-col">
      <PageHeader title="Monitor" theme={theme} onToggleTheme={onToggleTheme}>
        <div className="px-4 pb-2 flex items-center justify-between">
          <span className="text-xs text-faint">Auto-refreshing every 5s</span>
          <button
            type="button"
            onClick={handleRefresh}
            title="Refresh"
            className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-input transition-colors"
          >
            <svg className={`w-4 h-4 text-label ${refreshing ? "animate-spin-reverse" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
        </div>
      </PageHeader>

      <div className="flex-1 overflow-y-auto overflow-x-hidden">
      <div className="pb-24 p-4 space-y-5 max-w-2xl mx-auto w-full">
        {/* System Health */}
        <section>
          <h2 className="text-xs font-semibold text-dim uppercase tracking-wider mb-2">System Health</h2>
          {healthError && !health ? (
            <div className="rounded-xl bg-surface shadow-card p-4">
              <p className="text-sm text-red-400">Failed to reach health endpoint.</p>
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-3">
              <HealthCard label="Overall" status={health?.status || "unknown"} />
              <HealthCard label="Database" status={health?.db || "unknown"} />
              <HealthCard label="Claude CLI" status={health?.claude_cli || "unknown"} />
            </div>
          )}
        </section>

        {/* System Resources */}
        {sysStats && (
          <section>
            <h2 className="text-xs font-semibold text-dim uppercase tracking-wider mb-2">Resources</h2>
            <div className="space-y-3">
              {/* CPU / Memory / Disk bars */}
              <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
                {sysStats.cpu && (
                  <UsageBar
                    label={`CPU (${sysStats.cpu.cores} cores)`}
                    pct={sysStats.cpu.usage_pct}
                    detail={`Load ${sysStats.cpu.load_1m}`}
                  />
                )}
                {sysStats.memory && (
                  <UsageBar
                    label="Memory"
                    pct={sysStats.memory.usage_pct}
                    detail={`${sysStats.memory.used_gb} / ${sysStats.memory.total_gb} GB`}
                  />
                )}
                {sysStats.disk && (
                  <UsageBar
                    label="Disk"
                    pct={sysStats.disk.usage_pct}
                    detail={`${sysStats.disk.used_gb} / ${sysStats.disk.total_gb} GB`}
                  />
                )}
                {(sysStats.xylocopa || sysStats.agenthive) && (() => {
                  const proc = sysStats.xylocopa || sysStats.agenthive;
                  return (
                    <UsageBar
                      label="Xylocopa"
                      pct={sysStats.memory ? Math.min(Math.round(proc.mem_mb / (sysStats.memory.total_gb * 1024) * 100), 100) : 0}
                      detail={`${proc.mem_mb} MB / ${proc.cpu_pct}% CPU`}
                    />
                  );
                })()}
              </div>

              {/* GPUs */}
              {sysStats.gpus && sysStats.gpus.length > 0 && (
                <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
                  {sysStats.gpus.map((gpu) => (
                    <div key={gpu.index}>
                      <p className="text-xs text-label font-medium mb-2">
                        GPU {gpu.index}: {gpu.name}
                        <span className="text-dim ml-2">{gpu.temp_c}°C</span>
                      </p>
                      <div className="space-y-2">
                        <UsageBar label="Compute" pct={gpu.gpu_pct} detail={`${gpu.gpu_pct}%`} />
                        <UsageBar
                          label="VRAM"
                          pct={gpu.mem_pct}
                          detail={`${gpu.mem_used_mb} / ${gpu.mem_total_mb} MB`}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        )}

        {/* Token Usage — auto-refreshes every 10 min */}
        <TokenUsageSection tokenUsage={tokenUsage} onRefresh={refreshTokenUsage} />

        {/* Storage */}
        <StorageChart data={storageStats} onRefresh={refresh} />

        {/* Backup Management */}
        {backupInfo && (
          <section>
            <h2 className="text-xs font-semibold text-dim uppercase tracking-wider mb-2">Backup</h2>
            <div className="rounded-xl bg-surface shadow-card p-4 space-y-3">
              {/* Header row: status + action buttons */}
              <div className="flex items-center justify-between">
                <div className="min-w-0">
                  <p className="text-sm text-heading font-medium">
                    {backupInfo.backup_count} snapshots
                    {backupInfo.total_bytes > 0 && ` (${formatBytes(backupInfo.total_bytes)})`}
                  </p>
                  <p className="text-xs text-dim mt-0.5">
                    {backupInfo.enabled
                      ? `Auto: every ${backupInfo.interval_hours}h, keep ${backupInfo.max_backups} max`
                      : "Auto backup disabled"}
                  </p>
                </div>
                <div className="flex items-center gap-1.5">
                  {/* Settings gear */}
                  <button
                    type="button"
                    onClick={() => setBackupConfigOpen(!backupConfigOpen)}
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

              {/* Config panel (collapsible) */}
              {backupConfigOpen && (
                <div className="pt-2 border-t border-divider space-y-3">
                  {/* Enable/disable toggle */}
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-label">Auto backup</span>
                    <button
                      type="button"
                      onClick={() => handleUpdateConfig({ enabled: !backupInfo.enabled })}
                      className={`relative w-9 h-5 rounded-full transition-colors ${backupInfo.enabled ? "bg-cyan-500" : "bg-zinc-600"}`}
                    >
                      <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${backupInfo.enabled ? "left-[18px]" : "left-0.5"}`} />
                    </button>
                  </div>
                  {/* Interval */}
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-label">Interval</span>
                    <select
                      value={backupInfo.interval_hours}
                      onChange={(e) => handleUpdateConfig({ interval_hours: parseInt(e.target.value) })}
                      className="text-xs bg-input text-heading rounded-lg px-2 py-1 border border-divider"
                    >
                      <option value={1}>1h</option>
                      <option value={6}>6h</option>
                      <option value={12}>12h</option>
                      <option value={24}>24h</option>
                      <option value={48}>48h</option>
                    </select>
                  </div>
                  {/* Max backups */}
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-label">Keep max</span>
                    <select
                      value={backupInfo.max_backups}
                      onChange={(e) => handleUpdateConfig({ max_backups: parseInt(e.target.value) })}
                      className="text-xs bg-input text-heading rounded-lg px-2 py-1 border border-divider"
                    >
                      <option value={7}>7</option>
                      <option value={14}>14</option>
                      <option value={30}>30</option>
                      <option value={48}>48</option>
                      <option value={90}>90</option>
                    </select>
                  </div>
                </div>
              )}

              {/* Action buttons row */}
              <div className="flex items-center gap-1.5 pt-2 border-t border-divider">
                <button
                  type="button"
                  disabled={backupBusy}
                  onClick={handleManualBackup}
                  className="px-2 py-0.5 rounded text-[11px] font-medium bg-cyan-600/20 text-cyan-400 hover:bg-cyan-600/30 transition-colors disabled:opacity-50"
                >
                  {backupBusy ? "..." : "Backup"}
                </button>
                <button
                  type="button"
                  disabled={backupBusy}
                  onClick={handleImportBackup}
                  className="px-2 py-0.5 rounded text-[11px] font-medium bg-violet-600/20 text-violet-400 hover:bg-violet-600/30 transition-colors disabled:opacity-50"
                >
                  Import
                </button>
                {backupInfo.backup_count > 0 && (
                  <button
                    type="button"
                    onClick={() => setBackupListOpen(!backupListOpen)}
                    className="px-2 py-0.5 rounded text-[11px] font-medium bg-amber-600/20 text-amber-400 hover:bg-amber-600/30 transition-colors"
                  >
                    {backupListOpen ? "Hide" : "List"}
                  </button>
                )}
                <button
                  type="button"
                  disabled={orphanBusy}
                  onClick={handleOrphanClean}
                  className="px-2 py-0.5 rounded text-[11px] font-medium bg-orange-600/20 text-orange-400 hover:bg-orange-600/30 transition-colors disabled:opacity-50"
                >
                  {orphanBusy ? "..." : "Orphans"}
                </button>
                {backupInfo.backup_count > 0 && (
                  <button
                    type="button"
                    disabled={backupBusy}
                    onClick={handlePurgeBackups}
                    className="ml-auto px-2 py-0.5 rounded text-[11px] font-medium bg-red-600/20 text-red-400 hover:bg-red-600/30 transition-colors disabled:opacity-50"
                  >
                    Purge
                  </button>
                )}
              </div>

              {/* Backup list (collapsible) */}
              {backupListOpen && backupInfo.backups && (
                <div className="pt-2 border-t border-divider space-y-1 max-h-60 overflow-y-auto">
                  {backupInfo.backups.map((b) => (
                    <div key={b.name} className="flex items-center justify-between py-1.5 px-2 rounded-lg hover:bg-elevated/50 group">
                      <div className="min-w-0 flex-1">
                        <p className="text-xs text-heading font-mono truncate">
                          {new Date(b.timestamp).toLocaleString()}
                          {b.name.endsWith("_imported") && (
                            <span className="ml-1.5 text-violet-400 font-sans">(imported)</span>
                          )}
                        </p>
                        <p className="text-[10px] text-faint">
                          {formatBytes(b.total_bytes)}
                          {b.has_db && " · DB"}
                          {b.has_registry && " · Registry"}
                          {b.progress_count > 0 && ` · ${b.progress_count} progress`}
                        </p>
                      </div>
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <a
                          href={downloadBackupUrl(b.name)}
                          title="Download"
                          className="w-6 h-6 flex items-center justify-center rounded hover:bg-input"
                        >
                          <svg className="w-3.5 h-3.5 text-label" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                          </svg>
                        </a>
                        <button
                          type="button"
                          onClick={() => handleRestoreBackup(b.name)}
                          title="Restore"
                          className="w-6 h-6 flex items-center justify-center rounded hover:bg-input"
                        >
                          <svg className="w-3.5 h-3.5 text-cyan-400" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                          </svg>
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeleteBackup(b.name)}
                          title="Delete"
                          className="w-6 h-6 flex items-center justify-center rounded hover:bg-input"
                        >
                          <svg className="w-3.5 h-3.5 text-red-400" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        )}

        {/* Orphan result (button moved into backup card) */}
        {orphanResult && (
          <p className="text-xs text-dim">
            {orphanResult.error
              ? `Orphan cleanup error: ${orphanResult.error}`
              : orphanResult.message
                ? orphanResult.message
                : `Freed ${formatBytes(orphanResult.freed_bytes)} (${orphanResult.deleted_sessions} sessions, ${orphanResult.deleted_logs} logs, ${orphanResult.deleted_dirs} dirs, ${orphanResult.deleted_projects || 0} projects)`}
          </p>
        )}

        {/* Display: e-ink mode toggle */}
        <section className="rounded-xl bg-surface shadow-card p-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h3 className="text-sm font-medium text-heading">E-ink mode</h3>
              <p className="text-xs text-dim mt-1 leading-relaxed">
                Grayscale palette, no animations or blur — for e-paper
                displays (Bigme, BOOX, Kindle). Also enters fullscreen.
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={einkOn ? "true" : "false"}
              aria-label="Toggle e-ink mode"
              onClick={handleEinkToggle}
              className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
                einkOn ? "bg-cyan-500" : "bg-elevated"
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  einkOn ? "translate-x-6" : "translate-x-1"
                }`}
              />
            </button>
          </div>
        </section>

        {/* Help improve Xylocopa (telemetry toggle) */}
        <section className="rounded-xl bg-surface shadow-card p-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h3 className="text-sm font-medium text-heading">Help improve Xylocopa</h3>
              <p className="text-xs text-dim mt-1 leading-relaxed">
                A simple anonymous heartbeat helps me know the project is being used.
                No IPs, prompts, code, or paths.{" "}
                <a
                  href="https://github.com/jyao97/xylocopa#telemetry"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-cyan-500 hover:underline"
                >
                  Details
                </a>
              </p>
              {telemetry?.env_locked && (
                <p className="text-xs text-amber-500 mt-2">
                  Locked off via <code className="font-mono">XYLOCOPA_TELEMETRY</code> env var.
                </p>
              )}
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={telemetry?.enabled ? "true" : "false"}
              aria-label="Toggle anonymous telemetry"
              disabled={!telemetry || telemetry.env_locked || telemetryBusy}
              onClick={handleTelemetryToggle}
              className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                telemetry?.enabled ? "bg-cyan-500" : "bg-elevated"
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  telemetry?.enabled ? "translate-x-6" : "translate-x-1"
                }`}
              />
            </button>
          </div>
        </section>

      </div>
      </div>
    </div>
  );
}
