import { useState, useEffect, useCallback } from "react";
import { startDropboxLink, completeDropboxLink } from "../../lib/api";

/**
 * Three-step Dropbox link flow:
 *  1. Enter App key -> startDropboxLink -> authorize URL
 *  2. Open authorize URL, paste code -> completeDropboxLink -> account
 *  3. Connected confirmation -> Done
 */
export default function DropboxLinkModal({ open, initialAppKey, onClose, onLinked }) {
  const [step, setStep] = useState(1);
  const [appKey, setAppKey] = useState(initialAppKey || "");
  const [authorizeUrl, setAuthorizeUrl] = useState("");
  const [code, setCode] = useState("");
  const [account, setAccount] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Reset when re-opened
  useEffect(() => {
    if (open) {
      setStep(1);
      setAppKey(initialAppKey || "");
      setAuthorizeUrl("");
      setCode("");
      setAccount(null);
      setBusy(false);
      setError("");
    }
  }, [open, initialAppKey]);

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

  const handleStartLink = useCallback(async () => {
    const trimmed = appKey.trim();
    if (!trimmed) return;
    setBusy(true);
    setError("");
    try {
      const res = await startDropboxLink(trimmed);
      setAuthorizeUrl(res.authorize_url);
      setStep(2);
    } catch (err) {
      setError(err.message || "Failed to start link flow");
    } finally {
      setBusy(false);
    }
  }, [appKey]);

  const handleCompleteLink = useCallback(async () => {
    const trimmed = code.trim();
    if (!trimmed) return;
    setBusy(true);
    setError("");
    try {
      const res = await completeDropboxLink(trimmed);
      setAccount(res.account);
      setStep(3);
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-surface rounded-2xl p-6 max-w-sm w-full space-y-4 shadow-card">
        {step === 1 && (
          <>
            <h3 className="text-lg font-bold text-heading">Connect Dropbox</h3>
            <div className="text-xs text-dim space-y-1">
              <p>1. Go to <a href="https://www.dropbox.com/developers/apps" target="_blank" rel="noopener noreferrer" className="text-accent underline">dropbox.com/developers/apps</a></p>
              <p>2. Create app: Scoped access, App folder</p>
              <p>3. Permissions: files.metadata.read, files.content.read, files.content.write, account_info.read</p>
              <p>4. Submit, then copy the App key</p>
            </div>
            <input
              type="text"
              value={appKey}
              onChange={(e) => setAppKey(e.target.value)}
              placeholder="App key"
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
                disabled={busy || !appKey.trim()}
                onClick={handleStartLink}
                className="flex-1 min-h-[44px] rounded-lg bg-accent hover:opacity-90 text-accent-ink font-semibold text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {busy ? "Connecting..." : "Continue"}
              </button>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <h3 className="text-lg font-bold text-heading">Authorize</h3>
            <p className="text-xs text-dim">
              Click the button below to authorize in Dropbox, then paste the code you receive.
            </p>
            <button
              type="button"
              onClick={() => window.open(authorizeUrl, "_blank", "noopener,noreferrer")}
              className="w-full min-h-[44px] rounded-lg bg-accent hover:opacity-90 text-accent-ink font-semibold text-sm transition-colors"
            >
              Open Dropbox Authorization
            </button>
            <div className="text-xs text-dim space-y-1">
              <p>If the button didn't open a new tab, copy this URL:</p>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  readOnly
                  value={authorizeUrl}
                  className="flex-1 min-w-0 px-2 py-1 text-xs rounded bg-input text-body border border-divider font-mono truncate"
                  onFocus={(e) => e.target.select()}
                />
                <button
                  type="button"
                  onClick={() => navigator.clipboard?.writeText(authorizeUrl)}
                  className="shrink-0 px-2 py-1 rounded text-xs font-medium accent-tint-15 text-accent hover:accent-tint-25 transition-colors"
                >
                  Copy
                </button>
              </div>
            </div>
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
                onClick={handleCompleteLink}
                className="flex-1 min-h-[44px] rounded-lg bg-accent hover:opacity-90 text-accent-ink font-semibold text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {busy ? "Verifying..." : "Submit"}
              </button>
            </div>
          </>
        )}

        {step === 3 && (
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
