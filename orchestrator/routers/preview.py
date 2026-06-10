"""Web-app preview — serve agent-built static web apps for sandboxed in-chat
preview (iframe), with console-capture injection for the debug drawer.

Security model:
- Preview URLs carry a short-lived, preview-scoped token in the PATH (not a
  query param) so relative subresource references (./app.js, ./data.json)
  inherit it automatically inside the iframe.
- Preview-scoped tokens are rejected by the main auth middleware
  (auth.verify_token refuses tokens with a "scope" claim), so a leaked
  preview URL never grants general API access.
- Every response carries a CSP `sandbox` header, forcing an opaque origin
  even when a preview URL is opened directly in a new tab — agent-written JS
  can never read the app origin's localStorage (where the session JWT lives).
"""

import logging
import mimetypes
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from auth import create_token, decode_token, get_jwt_secret
from database import get_db
from route_helpers import get_project_or_404

logger = logging.getLogger("orchestrator")

router = APIRouter(tags=["preview"])

PREVIEW_TOKEN_TTL_MINUTES = 12 * 60

_PREVIEW_HEADERS = {
    # Force an opaque origin even on direct navigation — preview content must
    # never script against the app origin.
    "Content-Security-Policy": (
        "sandbox allow-scripts allow-forms allow-modals allow-popups "
        "allow-pointer-lock allow-downloads"
    ),
    # The opaque-origin document fetch()es its own data files with
    # Origin: null — allow it. Access control is the token in the path.
    "Access-Control-Allow-Origin": "*",
    # Agents iterate on these files; always serve fresh.
    "Cache-Control": "no-store",
}

# Injected into every served HTML document. Mirrors console.* and uncaught
# errors to the parent frame via postMessage so the preview panel can show a
# debug drawer. postMessage targets '*' because the sandboxed document runs
# in an opaque origin and cannot name its parent; the parent side filters by
# e.source === iframe.contentWindow.
_CONSOLE_CAPTURE_JS = """<script>
(function () {
  if (window.__xyConsoleHooked) return;
  window.__xyConsoleHooked = true;
  var MAX_LEN = 5000;
  function send(level, args) {
    var parts = [];
    for (var i = 0; i < args.length; i++) {
      var a = args[i];
      try {
        if (typeof a === "string") parts.push(a);
        else if (a instanceof Error) parts.push(a.stack || String(a));
        else parts.push(JSON.stringify(a));
      } catch (e) {
        try { parts.push(String(a)); } catch (e2) { parts.push("[unserializable]"); }
      }
    }
    var text = parts.join(" ");
    if (text.length > MAX_LEN) text = text.slice(0, MAX_LEN) + "…";
    try {
      window.parent.postMessage({ __xy_preview: 1, kind: "console", level: level, text: text, ts: Date.now() }, "*");
    } catch (e) {}
  }
  ["log", "info", "warn", "error", "debug"].forEach(function (m) {
    var orig = console[m];
    console[m] = function () {
      send(m, arguments);
      if (orig) orig.apply(console, arguments);
    };
  });
  window.addEventListener("error", function (e) {
    send("error", [e.message + " (" + (e.filename || "?") + ":" + (e.lineno || "?") + ")"]);
  });
  window.addEventListener("unhandledrejection", function (e) {
    var r = e.reason;
    send("error", ["Unhandled rejection: " + ((r && (r.stack || r.message)) || String(r))]);
  });
})();
</script>"""


def _inject_console_capture(html: str) -> str:
    """Insert the capture script as early as possible so it hooks console
    before the app's own scripts run."""
    lower = html.lower()
    for tag in ("<head>", "<head "):
        idx = lower.find(tag)
        if idx != -1:
            end = lower.find(">", idx)
            if end != -1:
                return html[: end + 1] + _CONSOLE_CAPTURE_JS + html[end + 1:]
    return _CONSOLE_CAPTURE_JS + html


@router.post("/api/preview/token")
async def mint_preview_token(payload: dict, db: Session = Depends(get_db)):
    """Mint a preview-scoped token for one project (session-token protected)."""
    project = (payload.get("project") or "").strip()
    proj = get_project_or_404(db, project)
    token = create_token(
        get_jwt_secret(db),
        expires_minutes=PREVIEW_TOKEN_TTL_MINUTES,
        extra_claims={"scope": "preview", "project": proj.name},
    )
    return {"token": token, "expires_in": PREVIEW_TOKEN_TTL_MINUTES * 60}


@router.get("/api/preview/t/{token}/{project}/{path:path}")
async def serve_preview_file(token: str, project: str, path: str,
                             db: Session = Depends(get_db)):
    """Serve a project file for sandboxed preview.

    Auth-middleware-exempt — the path-embedded preview token is validated
    here instead, so relative subresource requests authenticate without
    cookies or query params.
    """
    claims = decode_token(token, get_jwt_secret(db))
    if not claims or claims.get("scope") != "preview":
        raise HTTPException(status_code=401, detail="Invalid preview token")
    if claims.get("project") != project:
        raise HTTPException(status_code=403, detail="Token not valid for this project")

    proj = get_project_or_404(db, project)
    base_dir = os.path.realpath(proj.path)
    full_path = os.path.realpath(os.path.join(base_dir, path))
    # Strict containment — unlike /api/files/, no cross-project fallback
    # search here: the token is project-scoped and HTML is executable.
    if full_path != base_dir and not full_path.startswith(base_dir + os.sep):
        raise HTTPException(status_code=400, detail="Invalid path")
    if os.path.isdir(full_path):
        full_path = os.path.join(full_path, "index.html")
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")

    media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    if media_type == "text/html":
        with open(full_path, "rb") as f:
            html = f.read().decode("utf-8", errors="replace")
        return HTMLResponse(_inject_console_capture(html), headers=_PREVIEW_HEADERS)
    return FileResponse(full_path, media_type=media_type, headers=_PREVIEW_HEADERS)
