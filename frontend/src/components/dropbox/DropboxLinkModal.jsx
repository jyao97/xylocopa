import { useState, useEffect, useCallback } from "react";
import { startDropboxLink, completeDropboxLink } from "../../lib/api";

/**
 * Dropbox link modal with relay-based flow (default), direct redirect, and
 * paste-code fallback.
 *
 * Props:
 *   open           - boolean
 *   appKey         - string|falsy — when falsy, shows developer setup instructions
 *   linkMode       - "relay"|"direct"|"none" — from status.link_mode
 *   returnTo       - string — relative path for redirect after Dropbox approval
 *   onClose()      - close handler
 *   onLinked(account) - called after successful code-flow link
 */
export default function DropboxLinkModal({ open, appKey, linkMode, returnTo, onClose, onLinked }) {
  const [authorizeUrl, setAuthorizeUrl] = useState("");
  const [redirectUri, setRedirectUri] = useState("");
  const [code, setCode] = useState("");
  const [account, setAccount] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [step, setStep] = useState("default"); // "default" | "code" | "connected"
  const [copied, setCopied] = useState(false);
  const [startedMode, setStartedMode] = useState(null); // mode returned by start

  // Reset when re-opened
  useEffect(() => {
    if (open) {
      setAuthorizeUrl("");
      setRedirectUri("");
      setCode("");
      setAccount(null);
      setBusy(false);
      setError("");
      setStep("default");
      setCopied(false);
      setStartedMode(null);
    }
  }, [open]);

  // Escape to close
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  // Body scroll lock
  useEffect(() => {
    if (!open) return;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; };
  }, [open]);

  const computedRedirectUri = typeof window !== "undefined"
    ? `${window.location.origin}/api/dropbox/callback`
    : "/api/dropbox/callback";

  const handleCopyUri = useCallback(() => {
    navigator.clipboard?.writeText(computedRedirectUri);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [computedRedirectUri]);

  // Default flow: Continue to Dropbox (auto mode — relay or direct)
  const handleContinueRedirect = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const res = await startDropboxLink({ mode: "auto", returnTo });
      if (res.redirect_uri) setRedirectUri(res.redirect_uri);
      setStartedMode(res.mode || null);
      // Prefer relay_start_url when present (relay mode); fall back to authorize_url (direct)
      window.location.assign(res.relay_start_url || res.authorize_url);
    } catch (err) {
      setError(err.message || "Failed to start link flow");
      setBusy(false);
    }
  }, [returnTo]);

  // Code flow: start
  const handleStartCode = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const res = await startDropboxLink({ mode: "code" });
      setAuthorizeUrl(res.authorize_url);
      setStep("code");
    } catch (err) {
      setError(err.message || "Failed to start link flow");
    } finally {
      setBusy(false);
    }
  }, []);

  // Code flow: submit code
  const handleCompleteCode = useCallback(async () => {
    const trimmed = code.trim();
    if (!trimmed) return;
    setBusy(true);
    setError("");
    try {
      const res = await completeDropboxLink(trimmed);
      setAccount(res.account);
      setStep("connected");
    } catch (err) {
      setError(err.message || "Authorization failed");
    } finally {
      setBusy(false);
    }
  }, [code]);

  const handleDone = useCallback(() => {
    if (account) onLinked(account);
    onClose();
  }, [account, onLinked, onClose]);

  if (!open) return null;

  // Not configured view (link_mode === "none")
  if (linkMode === "none" || (!linkMode && !appKey)) {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4">
        <div className="bg-surface rounded-2xl p-6 max-w-sm w-full space-y-4 shadow-card">
          <h3 className="text-lg font-bold text-heading">Dropbox app key not configured</h3>
          <div className="text-xs text-dim space-y-2">
            <p>This build has no Dropbox app key. Set <code className="text-heading font-mono">DROPBOX_APP_KEY</code> in <code className="text-heading font-mono">.env</code> (your own app):</p>
            <ol className="list-decimal list-inside space-y-1">
              <li>Go to <a href="https://www.dropbox.com/developers/apps" target="_blank" rel="noopener noreferrer" className="text-accent underline">dropbox.com/developers/apps</a></li>
              <li>Create app: Scoped access, App folder</li>
              <li>Permissions tab: enable files.metadata.read, files.content.read, files.content.write, account_info.read</li>
              <li>OAuth 2 Redirect URIs: add the URI below (add one per origin, e.g. laptop and phone)</li>
              <li>Copy the App key, set <code className="text-heading font-mono">DROPBOX_APP_KEY=&lt;key&gt;</code> in <code className="text-heading font-mono">.env</code> and restart</li>
            </ol>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="text"
              readOnly
              value={computedRedirectUri}
              data-testid="redirect-uri"
              className="flex-1 min-w-0 px-2 py-1 text-xs rounded bg-input text-body border border-divider font-mono truncate"
              onFocus={(e) => e.target.select()}
            />
            <button
              type="button"
              onClick={handleCopyUri}
              className="shrink-0 px-2 py-1 rounded text-xs font-medium accent-tint-15 text-accent hover:accent-tint-25 transition-colors"
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-full min-h-[44px] rounded-lg bg-input hover:bg-elevated text-body text-sm transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    );
  }

  // Relay host label (shown after start in relay mode)
  let relayCaption = null;
  if (startedMode === "relay" && redirectUri) {
    let relayHost;
    try { relayHost = new URL(redirectUri).host; } catch { relayHost = redirectUri; }
    relayCaption = `Returns through ${relayHost}`;
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4">
      <div className="bg-surface rounded-2xl p-6 max-w-sm w-full space-y-4 shadow-card">
        {step === "default" && (
          <>
            <h3 className="text-lg font-bold text-heading">Connect Dropbox</h3>
            <p className="text-sm text-body">
              You'll be sent to Dropbox to sign in and approve access to its app folder, then brought back here.
            </p>
            {error && <p className="text-xs text-danger">{error}</p>}
            <div className="flex gap-3">
              <button
                type="button"
                onClick={onClose}
                className="flex-1 min-h-[44px] rounded-lg bg-input hover:bg-elevated text-body text-sm transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={handleContinueRedirect}
                className="flex-1 min-h-[44px] rounded-lg bg-accent hover:opacity-90 text-accent-ink font-semibold text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {busy ? "Redirecting..." : "Continue to Dropbox"}
              </button>
            </div>
            {relayCaption && (
              <p className="text-faint text-[11px]">{relayCaption}</p>
            )}
            <button
              type="button"
              onClick={handleStartCode}
              disabled={busy}
              className="text-xs text-accent hover:underline disabled:opacity-50"
            >
              Use a code instead
            </button>
          </>
        )}

        {step === "code" && (
          <>
            <h3 className="text-lg font-bold text-heading">Authorize</h3>
            <p className="text-xs text-dim">
              Open the Dropbox authorization page, approve access, then paste the code you receive.
            </p>
            <button
              type="button"
              onClick={() => window.open(authorizeUrl, "_blank", "noopener,noreferrer")}
              className="w-full min-h-[44px] rounded-lg bg-accent hover:opacity-90 text-accent-ink font-semibold text-sm transition-colors"
            >
              Open Dropbox Authorization
            </button>
            <input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="Paste the access code"
              className="w-full px-3 py-2 text-sm rounded-lg bg-input text-heading border border-divider placeholder-hint"
            />
            {error && <p className="text-xs text-danger">{error}</p>}
            <div className="flex gap-3">
              <button
                type="button"
                onClick={onClose}
                className="flex-1 min-h-[44px] rounded-lg bg-input hover:bg-elevated text-body text-sm transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={busy || !code.trim()}
                onClick={handleCompleteCode}
                className="flex-1 min-h-[44px] rounded-lg bg-accent hover:opacity-90 text-accent-ink font-semibold text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {busy ? "Verifying..." : "Submit"}
              </button>
            </div>
          </>
        )}

        {step === "connected" && (
          <>
            <h3 className="text-lg font-bold text-heading">Connected</h3>
            <p className="text-sm text-body">
              Connected as <span className="font-semibold text-heading">{account?.name}</span>{" "}
              ({account?.email})
            </p>
            <button
              type="button"
              onClick={handleDone}
              className="w-full min-h-[44px] rounded-lg bg-accent hover:opacity-90 text-accent-ink font-semibold text-sm transition-colors"
            >
              Done
            </button>
          </>
        )}
      </div>
    </div>
  );
}
