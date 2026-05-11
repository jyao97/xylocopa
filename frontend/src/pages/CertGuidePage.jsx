import { useState, useEffect } from "react";

const detectPlatform = () => {
  if (typeof navigator === "undefined") return "ios";
  const ua = navigator.userAgent;
  if (/Android/i.test(ua)) return "android";
  if (/iPhone|iPad|iPod/i.test(ua)) return "ios";
  return "ios";
};

const iosCertSteps = [
  {
    num: 1,
    title: "Download certificate",
    desc: "",
    link: { href: "/api/cert", label: "Tap here to download" },
  },
  {
    num: 2,
    title: "Install profile",
    desc: "Settings → General → VPN & Device Management → tap the downloaded profile → Install.",
  },
  {
    num: 3,
    title: "Enable trust",
    desc: "Settings → General → About → Certificate Trust Settings → toggle on \"mkcert\" or \"xylocopa\".",
  },
];

const iosPwaSteps = [
  {
    num: 1,
    title: "Open Share menu",
    desc: "Tap the Share button (square with up-arrow) at the bottom of Safari.",
  },
  {
    num: 2,
    title: "Add to Home Screen",
    desc: "Scroll down in the Share sheet, tap \"Add to Home Screen\", then tap \"Add\".",
  },
  {
    num: 3,
    title: "Done!",
    desc: "The Xylocopa icon appears on your Home Screen. Tap it and set your password.",
  },
];

const androidCertSteps = [
  {
    num: 1,
    title: "Download certificate",
    desc: "",
    link: { href: "/api/cert", label: "Tap here to download" },
  },
  {
    num: 2,
    title: "Set a screen lock first (if you haven't)",
    desc: "Android won't let you install a CA without a PIN, pattern, or password. Settings → Security → Screen lock.",
  },
  {
    num: 3,
    title: "Install certificate",
    desc: "Settings → Security → Encryption & credentials → Install a certificate → CA certificate → pick the downloaded file. Path varies by ROM — if you can't find it, search Settings for \"certificate\".",
  },
  {
    num: 4,
    title: "Confirm trust warning",
    desc: "Tap \"Install anyway\" — Android warns that user-installed CAs are less trusted; that's expected.",
  },
];

const androidPwaSteps = [
  {
    num: 1,
    title: "Open Chrome menu",
    desc: "Tap ⋮ (three dots, top right of Chrome).",
  },
  {
    num: 2,
    title: "Install app",
    desc: "Tap \"Install app\" or \"Add to Home screen\". Confirm.",
  },
  {
    num: 3,
    title: "Done!",
    desc: "The Xylocopa icon appears on your Home Screen. The bee icon loads from a public CDN, so it shows up correctly even though the server uses a private certificate.",
  },
];

// Sticky flag so AuthGuard knows we're mid-cert-install and should bounce
// the user back to /cert-guide instead of /login if the cert download
// causes a same-tab navigation away (PWA WebViews especially).
// Consumed once and TTL'd to 60s so it can't pollute normal navigation.
export const CERT_FLOW_KEY = "xy:cert-flow-pending";
export const CERT_FLOW_TTL_MS = 60_000;

function StepList({ steps, onLinkClick }) {
  return (
    <div className="space-y-4">
      {steps.map((s) => (
        <div key={s.num} className="flex gap-3">
          <div className="flex-shrink-0 w-7 h-7 rounded-full bg-cyan-600 text-white text-sm font-bold flex items-center justify-center">
            {s.num}
          </div>
          <div className="min-w-0">
            <div className="font-medium text-heading text-sm">{s.title}</div>
            <p className="text-xs text-dim mt-0.5">
              {s.desc}
              {s.link && (
                <>
                  {" "}
                  <a
                    href={s.link.href}
                    onClick={onLinkClick}
                    className="text-cyan-400 underline"
                  >
                    {s.link.label}
                  </a>
                  .
                </>
              )}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function CertGuidePage() {
  const [platform, setPlatform] = useState(detectPlatform);

  // We're back on cert-guide — clear any pending flag from a prior cert click.
  useEffect(() => {
    sessionStorage.removeItem(CERT_FLOW_KEY);
  }, []);

  const handleCertLinkClick = () => {
    sessionStorage.setItem(CERT_FLOW_KEY, String(Date.now()));
  };

  // Capture Chrome's beforeinstallprompt so the Android PWA install button
  // can fire the native install dialog directly. May or may not fire
  // depending on engagement heuristics — manual menu instructions stay
  // visible as a fallback.
  const [installPrompt, setInstallPrompt] = useState(null);
  useEffect(() => {
    const handler = (e) => {
      e.preventDefault();
      setInstallPrompt(e);
    };
    window.addEventListener("beforeinstallprompt", handler);
    return () => window.removeEventListener("beforeinstallprompt", handler);
  }, []);

  const handleAndroidInstall = async () => {
    if (!installPrompt) return;
    installPrompt.prompt();
    try {
      await installPrompt.userChoice;
    } finally {
      setInstallPrompt(null);
    }
  };

  // Fetch the cert SAN so the user can verify the URL they're using is
  // actually covered.  Tailscale-shared nodes get different CGNAT IPs in
  // each consuming tailnet — a mismatch here is the most common reason a
  // phone keeps showing "Not secure" even after installing the CA.
  const [certInfo, setCertInfo] = useState(null);
  useEffect(() => {
    let cancelled = false;
    fetch("/api/cert/info")
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (!cancelled) setCertInfo(data); })
      .catch(() => { /* ignore — card just won't render */ });
    return () => { cancelled = true; };
  }, []);

  const currentHost = typeof window !== "undefined" ? window.location.hostname : "";
  const covered = certInfo && (
    (certInfo.ips || []).includes(currentHost) ||
    (certInfo.dns || []).includes(currentHost)
  );

  const isAndroid = platform === "android";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto">
      <div className="absolute inset-0 bg-page/60 backdrop-blur-2xl" />

      <div className="relative z-10 w-full max-w-sm mx-4 my-8">
        {/* Platform toggle — UA detection isn't always right (e.g. desktop Chrome
            request-mobile-site, or testing iOS flow on Android), let users override. */}
        <div className="flex justify-center mb-4 gap-1 text-xs">
          <button
            type="button"
            onClick={() => setPlatform("ios")}
            className={`px-3 py-1 rounded-full transition-colors ${platform === "ios" ? "bg-cyan-600 text-white" : "bg-surface/50 text-dim hover:text-heading"}`}
          >
            iOS
          </button>
          <button
            type="button"
            onClick={() => setPlatform("android")}
            className={`px-3 py-1 rounded-full transition-colors ${platform === "android" ? "bg-cyan-600 text-white" : "bg-surface/50 text-dim hover:text-heading"}`}
          >
            Android
          </button>
        </div>

        {/* Cert coverage panel — tells the user whether the URL they're on
            is actually in the cert SAN.  Tailscale-shared nodes have
            different CGNAT IPs per consuming tailnet, so a mismatch here is
            the most common cause of "Not secure" after CA install. */}
        {certInfo && (
          <div className={`rounded-2xl backdrop-blur-md border p-4 mb-5 shadow-lg ${
            covered
              ? "bg-surface/60 border-divider/50"
              : "bg-amber-500/10 border-amber-500/40"
          }`}>
            <div className="flex items-start gap-2 mb-2">
              <span className={covered ? "text-cyan-400" : "text-amber-400"}>
                {covered ? "✓" : "⚠"}
              </span>
              <div className="text-sm font-medium text-heading">
                {covered
                  ? "This URL is covered by the certificate"
                  : "This URL is NOT covered by the certificate"}
              </div>
            </div>
            <div className="text-xs text-dim mb-2">
              Currently accessing via <span className="font-mono text-heading">{currentHost}</span>
            </div>
            <div className="text-xs text-dim mb-1">Certificate is valid for:</div>
            <div className="flex flex-wrap gap-1.5">
              {[...(certInfo.ips || []), ...(certInfo.dns || [])]
                .filter(h => h !== "127.0.0.1" && h !== "localhost" && h !== "xylocopa")
                .map(h => (
                  <span
                    key={h}
                    className={`px-2 py-0.5 rounded-md text-xs font-mono ${
                      h === currentHost
                        ? "bg-cyan-500/20 text-cyan-200"
                        : "bg-surface/80 text-dim"
                    }`}
                  >
                    {h}
                  </span>
                ))}
            </div>
            {!covered && (
              <div className="text-xs text-amber-200/80 mt-3 leading-relaxed">
                Installing the CA on this device will trust the certificate, but
                browsers will still warn because the URL host isn't in the SAN.
                Bookmark one of the addresses above instead, or ask the server
                admin to re-run <span className="font-mono">tools/ensure-cert.sh</span>{" "}
                with this host added.
              </div>
            )}
          </div>
        )}

        {/* Section 1: CA Certificate (must be done first) */}
        <div className="text-center mb-5">
          <h1 className="text-lg font-semibold text-heading">Step 1: Trust Certificate</h1>
          <p className="text-sm text-dim mt-1">Required for voice input, file uploads, and the app to work without warnings</p>
        </div>

        <div className="rounded-2xl bg-surface/60 backdrop-blur-md border border-divider/50 p-5 shadow-lg">
          <StepList
            steps={isAndroid ? androidCertSteps : iosCertSteps}
            onLinkClick={handleCertLinkClick}
          />
        </div>

        {/* Section 2: Add to Home Screen */}
        <div className="text-center mt-8 mb-5">
          <h2 className="text-base font-semibold text-heading">Step 2: Add to Home Screen</h2>
          <p className="text-sm text-dim mt-1">
            {isAndroid ? "Install as a PWA from Chrome" : "Install as a PWA from Safari"}
          </p>
        </div>

        <div className="rounded-2xl bg-surface/60 backdrop-blur-md border border-divider/50 p-5 shadow-lg">
          {isAndroid ? (
            <>
              <StepList steps={androidPwaSteps} />

              {/* If Chrome already fired beforeinstallprompt, give a one-tap
                  install button. Otherwise the user follows the manual
                  steps above. */}
              {installPrompt && (
                <button
                  type="button"
                  onClick={handleAndroidInstall}
                  className="mt-4 w-full py-3 rounded-xl font-medium bg-cyan-600 hover:bg-cyan-500 active:scale-[0.98] text-white transition-all"
                >
                  Install Xylocopa App
                </button>
              )}
            </>
          ) : (
            <StepList steps={iosPwaSteps} />
          )}
        </div>

        <p className="text-center mt-4">
          <a href="/login" className="text-sm text-cyan-400 hover:underline">
            Back to login
          </a>
        </p>
      </div>
    </div>
  );
}
