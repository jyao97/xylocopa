// Shared primitives for "is this media file actually there?" checks across
// FilePreview, ImageLightbox, and ProjectBrowserModal. The backend
// /api/files/exists-batch endpoint backs `useBatchExists`.
//
// Why centralized: each component used to roll its own "is the file gone"
// detection (cache-bust + onError heuristics, or nothing at all), which led
// to false positives, false negatives, and inconsistent UI. Moving the
// existence probe + cache-bust here means all three surfaces share one
// signal and one fallback UX.

import { useState, useEffect, useMemo } from "react";
import { filesExistsBatch } from "./api";

// Parse a media URL into the shape expected by /api/files/exists-batch.
// Strips ?query (notably the ?token=… that fileUrl() appends) so the
// backend doesn't see "<path>?token=..." as the filename. Returns null
// for URLs we can't probe (http://, data:, blob:) — caller treats those
// as always-exists.
export function parseFileUrl(url) {
  if (!url) return null;
  const noQuery = url.split("?")[0];
  let m = noQuery.match(/^\/?api\/files\/([^/]+)\/(.+)$/);
  if (m) return { project: decodeURIComponent(m[1]), path: decodeURIComponent(m[2]) };
  m = noQuery.match(/^\/?api\/uploads\/(.+)$/);
  if (m) return { upload: decodeURIComponent(m[1]) };
  return null;
}

// Batch-probe existence + size + mtime for a list of URLs. One round-trip
// replaces N HEAD probes (and HEAD isn't auto-registered on @router.get
// anyway, so per-row probes used to come back 405). Returns a map keyed by
// URL with { exists, size, mtime }; URLs that don't parse are absent
// (caller treats them as exists).
export function useBatchExists(urls) {
  const [statMap, setStatMap] = useState({});
  const probeKeys = useMemo(() => {
    if (!urls) return [];
    return urls.filter((u) => parseFileUrl(u)).sort();
  }, [urls]);
  const cacheKey = probeKeys.join("|");
  useEffect(() => {
    if (!probeKeys.length) { setStatMap({}); return; }
    let cancelled = false;
    const items = probeKeys.map((u) => parseFileUrl(u));
    filesExistsBatch(items)
      .then((resp) => {
        if (cancelled) return;
        const next = {};
        const results = resp?.results || [];
        probeKeys.forEach((u, i) => { next[u] = results[i] || { exists: false }; });
        setStatMap(next);
      })
      .catch(() => { if (!cancelled) setStatMap({}); });
    return () => { cancelled = true; };
  }, [cacheKey]); // eslint-disable-line react-hooks/exhaustive-deps
  return statMap;
}

// Single-URL convenience. Returns:
//   null while probing,
//   { exists: true, size, mtime } / { exists: false, ... } once known,
//   { exists: true } for non-parseable URLs (optimistic — can't probe).
export function useFileExists(url) {
  const urls = useMemo(() => (url ? [url] : []), [url]);
  const map = useBatchExists(urls);
  if (!url) return null;
  if (!parseFileUrl(url)) return { exists: true };
  return map[url] || null;
}

// Append a cache-bust query param. Pass the file's mtime (from a stat
// result) when available — the URL only changes when the file actually
// changes, so the browser cache stays useful. Pass null/undefined to skip.
export function withCacheBust(url, version) {
  if (!url || version == null) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}_v=${version}`;
}
