/**
 * Formatting helpers for the Dropbox sync UI.
 * Intentionally duplicates MonitorPage's formatBytes behaviour (B/KB/MB/GB/TB,
 * one decimal above KB) so we don't refactor MonitorPage.
 */

export function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const val = bytes / Math.pow(1024, i);
  return `${val < 10 ? val.toFixed(1) : Math.round(val)} ${units[i]}`;
}

export function formatRelative(iso) {
  if (!iso) return "never";
  const parsed = new Date(iso).getTime();
  if (Number.isNaN(parsed)) return "never";
  const ms = Date.now() - parsed;
  const absMs = Math.abs(ms);
  const absSec = Math.floor(absMs / 1000);
  if (absSec < 30) return "just now";
  const future = ms < 0;
  const min = Math.floor(absSec / 60);
  if (min < 60) return future ? `in ${min} min` : `${min} min ago`;
  const hrs = Math.floor(min / 60);
  if (hrs < 24) return future ? `in ${hrs} h` : `${hrs} h ago`;
  const days = Math.floor(hrs / 24);
  return future ? `in ${days} d` : `${days} d ago`;
}
