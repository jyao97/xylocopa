import { useState, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { mintPreviewToken } from "../lib/api";
import { previewUrl } from "../lib/urls";

// Sandbox WITHOUT allow-same-origin: the app runs in an opaque origin and
// cannot read the parent's localStorage (where the session JWT lives). The
// backend additionally sends a CSP sandbox header so even "open in new tab"
// stays opaque-origin.
const SANDBOX = "allow-scripts allow-forms allow-modals allow-popups allow-pointer-lock allow-downloads";

const LEVEL_STYLES = {
  error: "text-red-400",
  warn: "text-amber-400",
  info: "text-body",
  log: "text-body",
  debug: "text-dim",
};

function ConsoleDrawer({ logs, onClear }) {
  const endRef = useRef(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [logs.length]);

  return (
    <div className="h-[35%] min-h-[120px] border-t border-divider bg-page flex flex-col">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-divider">
        <span className="text-xs text-dim uppercase tracking-wide">Console</span>
        <button
          type="button"
          onClick={onClear}
          className="text-xs text-dim hover:text-label px-2 py-0.5 rounded hover:bg-hover transition-colors"
        >
          Clear
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-3 py-1.5 font-mono text-xs space-y-0.5">
        {logs.length === 0 && <div className="text-dim italic">No console output yet</div>}
        {logs.map((l, i) => (
          <div key={i} className={`whitespace-pre-wrap break-words ${LEVEL_STYLES[l.level] || "text-body"}`}>
            <span className="text-dim select-none">{l.level === "error" ? "✕ " : l.level === "warn" ? "⚠ " : "› "}</span>
            {l.text}
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}

/**
 * Fullscreen sandboxed preview of an agent web app, with a console drawer
 * fed by the capture script the backend injects into served HTML.
 *
 * Two source modes:
 *  - static: pass project + path → a preview-scoped token is minted and the
 *    iframe loads /api/preview/t/{token}/...
 *  - direct: pass src (stable port-proxy prefix from webapp_present
 *    metadata) → used as-is, no mint.
 */
export default function WebAppPreview({ project, path, src: directSrc, filename, onClose, onMinimize, hidden }) {
  const [src, setSrc] = useState(directSrc || null);
  const [error, setError] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [showConsole, setShowConsole] = useState(false);
  const [logs, setLogs] = useState([]);
  const [frameKey, setFrameKey] = useState(0);
  const iframeRef = useRef(null);
  const errorCount = logs.filter((l) => l.level === "error").length;

  // Mint a preview-scoped token, then point the iframe at the path-token URL.
  useEffect(() => {
    if (directSrc) {
      setSrc(directSrc);
      return undefined;
    }
    let cancelled = false;
    mintPreviewToken(project)
      .then(({ token }) => {
        if (!cancelled) setSrc(previewUrl(token, project, path));
      })
      .catch((e) => {
        if (!cancelled) setError(e.message || "Failed to start preview");
      });
    return () => { cancelled = true; };
  }, [project, path, directSrc]);

  // Console messages from the sandboxed iframe (opaque origin → filter by source).
  useEffect(() => {
    const onMessage = (e) => {
      const d = e.data;
      if (!d || d.__xy_preview !== 1 || d.kind !== "console") return;
      if (iframeRef.current && e.source !== iframeRef.current.contentWindow) return;
      setLogs((prev) => [...prev.slice(-499), { level: d.level, text: String(d.text ?? "") }]);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  useEffect(() => {
    if (hidden) return undefined;
    const onKey = (e) => { if (e.key === "Escape") (onMinimize || onClose)(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onMinimize, hidden]);

  const reload = () => {
    setLoaded(false);
    setLogs([]);
    setFrameKey((k) => k + 1);
  };

  return createPortal(
    // `invisible` (not unmount) when minimized — the iframe stays mounted so
    // the app's JS/WebGL state survives and restore is instant.
    <div
      className={`fixed inset-0 z-50 flex flex-col bg-page ${hidden ? "invisible pointer-events-none" : ""}`}
      aria-hidden={hidden || undefined}
      style={{ paddingTop: "env(safe-area-inset-top, 0px)" }}
    >
      {/* Toolbar */}
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-divider shrink-0">
        <svg className="w-4 h-4 text-cyan-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9 9 0 100-18 9 9 0 000 18zm0 0c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3 7.5 7.03 7.5 12s2.015 9 4.5 9zM3.6 9h16.8M3.6 15h16.8" />
        </svg>
        <span className="text-sm text-label truncate flex-1 min-w-0" title={path}>{filename}</span>
        <button
          type="button"
          onClick={reload}
          title="Reload app"
          className="p-1.5 rounded hover:bg-hover transition-colors text-dim hover:text-label"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
          </svg>
        </button>
        {src && (
          <a
            href={src}
            target="_blank"
            rel="noopener noreferrer"
            title="Open in new tab"
            className="p-1.5 rounded hover:bg-hover transition-colors text-dim hover:text-label"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
            </svg>
          </a>
        )}
        <button
          type="button"
          onClick={() => setShowConsole((v) => !v)}
          title="Toggle console"
          className={`relative p-1.5 rounded hover:bg-hover transition-colors ${showConsole ? "text-cyan-400" : "text-dim hover:text-label"}`}
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0021 18V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 003 6v12a2.25 2.25 0 002.25 2.25z" />
          </svg>
          {errorCount > 0 && (
            <span className="absolute -top-0.5 -right-0.5 min-w-[14px] h-[14px] px-0.5 rounded-full bg-red-500 text-white text-[9px] leading-[14px] text-center font-medium">
              {errorCount > 99 ? "99+" : errorCount}
            </span>
          )}
        </button>
        {onMinimize && (
          <button
            type="button"
            onClick={onMinimize}
            title="Minimize — keeps the app running"
            className="p-1.5 rounded hover:bg-hover transition-colors text-dim hover:text-label"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 13.5L12 21m0 0l-7.5-7.5M12 21V3" />
            </svg>
          </button>
        )}
        <button
          type="button"
          onClick={onClose}
          title="Close preview"
          className="p-1.5 rounded hover:bg-hover transition-colors text-dim hover:text-label"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* App frame */}
      <div className="flex-1 relative min-h-0">
        {error && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-sm text-red-400">{error}</span>
          </div>
        )}
        {!error && !loaded && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-sm text-dim animate-pulse">Loading app…</span>
          </div>
        )}
        {src && !error && (
          <iframe
            key={frameKey}
            ref={iframeRef}
            src={src}
            sandbox={SANDBOX}
            title={filename}
            onLoad={() => setLoaded(true)}
            className={`w-full h-full border-0 bg-white ${loaded ? "" : "opacity-0"}`}
          />
        )}
      </div>

      {showConsole && <ConsoleDrawer logs={logs} onClear={() => setLogs([])} />}
    </div>,
    document.body
  );
}

// ---------------------------------------------------------------------------
// Per-chat dock — keeps minimized web apps mounted so heavy apps (3DGS
// viewers, WebGL scenes) restore instantly instead of reloading. Scoped to
// one agent chat: navigating away or switching agents tears everything down.
// ---------------------------------------------------------------------------

const DOCK_OPEN_EVENT = "xy-webapp-dock-open";

/** Ask the chat's dock to open/restore an app. Returns false if no dock for
 *  this agent is mounted (caller falls back to a local one-shot panel). */
function requestDockOpen(agentId, app) {
  const detail = { agentId, app, handled: false };
  window.dispatchEvent(new CustomEvent(DOCK_OPEN_EVENT, { detail }));
  return detail.handled;
}

export function WebAppDock({ agentId }) {
  const [apps, setApps] = useState([]); // [{ key, app, minimized }]

  // Switching to another agent's chat counts as closing this chat.
  useEffect(() => { setApps([]); }, [agentId]);

  useEffect(() => {
    const onOpen = (e) => {
      if (e.detail.agentId !== agentId) return;
      e.detail.handled = true;
      const app = e.detail.app;
      const key = `${app.kind}:${app.project}:${app.path || app.src}`;
      // One fullscreen panel at a time: opening one minimizes the others.
      setApps((prev) => prev.some((x) => x.key === key)
        ? prev.map((x) => ({ ...x, minimized: x.key !== key }))
        : [...prev.map((x) => ({ ...x, minimized: true })), { key, app, minimized: false }]);
    };
    window.addEventListener(DOCK_OPEN_EVENT, onOpen);
    return () => window.removeEventListener(DOCK_OPEN_EVENT, onOpen);
  }, [agentId]);

  const minimize = (key) => setApps((p) => p.map((x) => (x.key === key ? { ...x, minimized: true } : x)));
  const restore = (key) => setApps((p) => p.map((x) => ({ ...x, minimized: x.key !== key })));
  const close = (key) => setApps((p) => p.filter((x) => x.key !== key));

  const chips = apps.filter((x) => x.minimized);

  return (
    <>
      {apps.map(({ key, app, minimized }) => (
        <WebAppPreview
          key={key}
          project={app.project}
          path={app.path}
          src={app.src}
          filename={app.filename}
          hidden={minimized}
          onMinimize={() => minimize(key)}
          onClose={() => close(key)}
        />
      ))}
      {chips.length > 0 && createPortal(
        <div
          className="fixed left-3 z-40 flex flex-wrap gap-2 max-w-[calc(100vw-24px)]"
          style={{ bottom: "calc(env(safe-area-inset-bottom, 0px) + 96px)" }}
        >
          {chips.map(({ key, app }) => (
            <div
              key={key}
              onClick={() => restore(key)}
              title="Restore app"
              className="flex items-center gap-1.5 pl-2.5 pr-1.5 py-1.5 rounded-full bg-elevated border border-divider shadow-card cursor-pointer hover:bg-hover transition-colors"
            >
              <svg className="w-3.5 h-3.5 text-cyan-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9 9 0 100-18 9 9 0 000 18zm0 0c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3 7.5 7.03 7.5 12s2.015 9 4.5 9zM3.6 9h16.8M3.6 15h16.8" />
              </svg>
              <span className="text-xs text-label max-w-[120px] truncate">{app.filename}</span>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); close(key); }}
                title="Close app"
                className="p-0.5 rounded-full hover:bg-hover text-dim hover:text-label"
              >
                <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          ))}
        </div>,
        document.body
      )}
    </>
  );
}

/**
 * Chat bubble for kind="webapp" messages (posted by the MCP webapp_present
 * tool). metadata.webapp = { kind, target, project, title, description,
 * src? } — src is the stable proxy prefix (port) or external URL (url).
 */
export function WebAppCardBubble({ message, agentId }) {
  const [open, setOpen] = useState(false);
  const app = message.metadata?.webapp || {};
  const isUrl = app.kind === "url";
  const label = app.title
    || (app.kind === "port" ? `localhost:${app.target}` : (app.target || "").split("/").pop());
  const sub = isUrl ? app.target
    : app.kind === "port" ? `local service · port ${app.target}`
    : app.target;

  const handleOpen = () => {
    if (isUrl) {
      window.open(app.target, "_blank", "noopener,noreferrer");
      return;
    }
    const spec = {
      kind: app.kind,
      project: app.project,
      path: app.kind === "static" ? app.target : undefined,
      src: app.kind === "port" ? app.src : undefined,
      filename: label,
    };
    // Prefer the chat's dock (minimizable, state survives); fall back to a
    // local one-shot panel if no dock is mounted.
    if (!requestDockOpen(agentId, spec)) setOpen(true);
  };

  return (
    <div className="flex justify-start my-2" data-msg-id={message.id} data-msg-type="webapp">
      <div
        onClick={handleOpen}
        className="rounded-2xl rounded-bl-md bg-surface shadow-card overflow-hidden max-w-[min(85%,20rem)] cursor-pointer hover:bg-hover transition-colors"
      >
        <div className="flex items-center gap-2.5 px-4 py-3">
          <svg className="w-5 h-5 text-cyan-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9 9 0 100-18 9 9 0 000 18zm0 0c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3 7.5 7.03 7.5 12s2.015 9 4.5 9zM3.6 9h16.8M3.6 15h16.8" />
          </svg>
          <div className="flex-1 min-w-0">
            <span className="text-sm text-label truncate block">{label}</span>
            <span className="text-xs text-dim truncate block">{sub}</span>
            {app.description && (
              <span className="text-xs text-dim/80 mt-0.5 line-clamp-2 block">{app.description}</span>
            )}
          </div>
          {isUrl ? (
            <svg className="w-4 h-4 text-dim shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
            </svg>
          ) : (
            <div className="w-7 h-7 rounded-full bg-cyan-500/15 flex items-center justify-center shrink-0">
              <svg className="w-3.5 h-3.5 ml-px text-cyan-400" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 5v14l11-7z" />
              </svg>
            </div>
          )}
        </div>
      </div>
      {open && !isUrl && (
        <WebAppPreview
          project={app.project}
          path={app.kind === "static" ? app.target : undefined}
          src={app.kind === "port" ? app.src : undefined}
          filename={label}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}
