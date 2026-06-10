/**
 * Centralized API URL builders and backend path patterns.
 *
 * All resource URL construction goes through these helpers so that
 * auth mechanisms (signed URLs, cookies) or platform changes (macOS)
 * only require updating this single file.
 */

import { getAuthToken } from "./api";

// ---------------------------------------------------------------------------
// API path prefixes
// ---------------------------------------------------------------------------
export const API_FILES_PREFIX = "/api/files/";
export const API_THUMBS_PREFIX = "/api/thumbs/";
export const API_UPLOADS_PREFIX = "/api/uploads/";
export const API_PREVIEW_PREFIX = "/api/preview/t/";

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/** Append auth token as query parameter for browser-initiated requests (<img>, <video>, <a>). */
function withToken(url) {
  const token = getAuthToken();
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}

// ---------------------------------------------------------------------------
// URL builders
// ---------------------------------------------------------------------------

/** Build URL for a user-uploaded file. */
export function uploadUrl(filename) {
  return withToken(`${API_UPLOADS_PREFIX}${encodeURIComponent(filename)}`);
}

/** Build URL for a project file. */
export function fileUrl(project, relPath) {
  const segments = relPath.split("/").map(encodeURIComponent).join("/");
  return withToken(`${API_FILES_PREFIX}${encodeURIComponent(project)}/${segments}`);
}

/** Build URL for a project file thumbnail. */
export function thumbUrl(project, relPath) {
  const segments = relPath.split("/").map(encodeURIComponent).join("/");
  return withToken(`${API_THUMBS_PREFIX}${encodeURIComponent(project)}/${segments}`);
}

/** Build URL for a sandboxed web-app preview. The preview-scoped token rides
 *  in the path (not a query param) so relative subresource refs inherit it. */
export function previewUrl(token, project, relPath) {
  const segments = relPath.split("/").map(encodeURIComponent).join("/");
  return `${API_PREVIEW_PREFIX}${encodeURIComponent(token)}/${encodeURIComponent(project)}/${segments}`;
}

/** Convert an /api/files/ URL to its /api/thumbs/ equivalent. */
export function fileUrlToThumbUrl(url) {
  if (!url.startsWith(API_FILES_PREFIX)) return url;
  return API_THUMBS_PREFIX + url.slice(API_FILES_PREFIX.length);
}

// ---------------------------------------------------------------------------
// Backend path patterns (for recognizing backend filesystem paths)
// ---------------------------------------------------------------------------

/** Matches `.xylocopa/uploads/` (or legacy `.agenthive/uploads/`)`<filename>`. Group 1 = filename. */
export const RE_UPLOADS_PATH = /\.(?:xylocopa|agenthive)\/uploads\/([^/]+)$/;

/** Matches `xylocopa-projects/` (or legacy `agenthive-projects/`)`<project>/<rest>`. Groups: 1=project, 2=rest. */
export const RE_PROJECTS_PATH = /(?:xylocopa|agenthive)-projects\/([^/]+)\/(.+)/;

/** Segment string for checking if a path references xylocopa-projects (preferred). */
export const PROJECTS_DIR_SEGMENT = "xylocopa-projects/";

/** Legacy segment retained so paths that still reference agenthive-projects are recognized. */
export const LEGACY_PROJECTS_DIR_SEGMENT = "agenthive-projects/";
