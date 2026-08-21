import { useState, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { mintPreviewToken } from "../lib/api";
import { previewUrl } from "../lib/urls";
import { useWsEvent } from "../hooks/useWebSocket";
import WebAppDock from "./WebAppDock";

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
      if (iframeRef.current && e.source !== iframeRef.current.contentWindow) return;
      // Keyboard-focus delegation: a sandboxed (opaque-origin) iframe cannot
      // take keyboard focus by itself — even clicks inside it leave focus on
      // the parent document, so key-driven apps (game/world-model demos) go
      // deaf. The page posts this request on pointerdown; focusing the
      // iframe ELEMENT routes keystrokes into the inner browsing context.
      if (e.data === "xylo:focus-preview") { iframeRef.current?.focus(); return; }
      const d = e.data;
      if (!d || d.__xy_preview !== 1 || d.kind !== "console") return;
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
        <svg className="w-4 h-4 text-accent shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
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
          className={`relative p-1.5 rounded hover:bg-hover transition-colors ${showConsole ? "text-accent" : "text-dim hover:text-label"}`}
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
            onLoad={() => { setLoaded(true); iframeRef.current?.focus(); }}
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
// Global dock — keeps minimized web apps mounted so heavy apps (3DGS
// viewers, WebGL scenes) restore instantly instead of reloading. Apps
// survive navigation; an app is torn down only when its agent fully stops
// (agent_update → STOPPED/ERROR) or the user closes it explicitly.
// ---------------------------------------------------------------------------

let _dockApps = []; // [{ key, agentId, app, minimized }]
let _dockHostCount = 0;
const _dockListeners = new Set();
const _dockNotify = () => { for (const l of _dockListeners) l(_dockApps); };

/** Open (or restore) an app in the global dock. Returns false when no host
 *  is mounted — caller falls back to a local one-shot panel. */
function dockOpenApp(agentId, app) {
  if (_dockHostCount === 0) return false;
  const key = `${agentId}:${app.kind}:${app.project}:${app.path || app.src}`;
  // One fullscreen panel at a time: opening one minimizes the others.
  _dockApps = _dockApps.some((x) => x.key === key)
    ? _dockApps.map((x) => ({ ...x, minimized: x.key !== key }))
    : [..._dockApps.map((x) => ({ ...x, minimized: true })), { key, agentId, app, minimized: false }];
  _dockNotify();
  return true;
}

export function WebAppDockHost() {
  const [apps, setApps] = useState(_dockApps);

  useEffect(() => {
    _dockHostCount += 1;
    const listener = (a) => setApps(a);
    _dockListeners.add(listener);
    setApps(_dockApps);
    return () => {
      _dockHostCount -= 1;
      _dockListeners.delete(listener);
    };
  }, []);

  // An agent that fully stops takes its mounted apps with it. IDLE is the
  // normal between-turns state and must not tear anything down.
  useWsEvent((event) => {
    if (event.type !== "agent_update") return;
    const { agent_id: aid, status } = event.data || {};
    if ((status === "STOPPED" || status === "ERROR") && _dockApps.some((x) => x.agentId === aid)) {
      _dockApps = _dockApps.filter((x) => x.agentId !== aid);
      _dockNotify();
    }
  });

  const minimize = (key) => { _dockApps = _dockApps.map((x) => (x.key === key ? { ...x, minimized: true } : x)); _dockNotify(); };
  const restore = (key) => { _dockApps = _dockApps.map((x) => ({ ...x, minimized: x.key !== key })); _dockNotify(); };
  const close = (key) => { _dockApps = _dockApps.filter((x) => x.key !== key); _dockNotify(); };

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
      <WebAppDock chips={chips} onRestore={restore} onClose={close} />
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
    // Prefer the global dock (minimizable, survives navigation); fall back
    // to a local one-shot panel if no host is mounted.
    if (!dockOpenApp(agentId, spec)) setOpen(true);
  };

  return (
    <div className="flex justify-start my-2" data-msg-id={message.id} data-msg-type="webapp">
      <div
        onClick={handleOpen}
        className="rounded-2xl rounded-bl-md bg-surface shadow-card overflow-hidden max-w-[min(85%,20rem)] cursor-pointer hover:bg-hover transition-colors"
      >
        <div className="flex items-center gap-2.5 px-4 py-3">
          <svg className="w-5 h-5 text-accent shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
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
            <div className="w-7 h-7 rounded-full accent-tint-15 flex items-center justify-center shrink-0">
              <svg className="w-3.5 h-3.5 ml-px text-accent" fill="currentColor" viewBox="0 0 24 24">
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
