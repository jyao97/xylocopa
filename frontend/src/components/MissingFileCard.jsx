// Shared "file is gone" fallback UI. Used by FilePreview chips,
// ImageLightbox overlay, and ProjectBrowserModal viewer — anywhere
// useFileExists / useBatchExists reports exists=false.
//
// Two visual variants:
//   default — light card on elevated surface (matches chat thumb chips)
//   dark    — translucent dark card (lightbox/full-screen contexts)
//
// When `onRetry` is provided, the card becomes clickable and renders a
// refresh icon — for the "file briefly disappeared during a sweep/deploy
// and might be back now" case. Without onRetry the card is inert.

const FileIconSvg = ({ className }) => (
  <svg className={className} fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
  </svg>
);

const RefreshIconSvg = ({ className }) => (
  <svg className={className} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
  </svg>
);

export default function MissingFileCard({ filename, originalPath, dark = false, onRetry }) {
  const handleRetry = (e) => {
    e.stopPropagation();
    e.preventDefault();
    onRetry?.();
  };

  if (dark) {
    return (
      <div
        className={`inline-flex items-center gap-2 px-4 py-3 rounded-lg bg-black/60 max-w-[80vw] ${onRetry ? "cursor-pointer hover:bg-black/70 transition-colors" : ""}`}
        title={originalPath || filename}
        onClick={onRetry ? handleRetry : undefined}
      >
        <FileIconSvg className="w-5 h-5 text-white/60 shrink-0" />
        <span className="text-sm text-white/90 truncate flex-1 min-w-0">{filename}</span>
        <span className="text-xs text-white/60 uppercase shrink-0">missing</span>
        {onRetry && (
          <button
            type="button"
            onClick={handleRetry}
            title="Check again"
            className="p-1 rounded hover:bg-white/10 transition-colors text-white/70 hover:text-white shrink-0"
          >
            <RefreshIconSvg className="w-4 h-4" />
          </button>
        )}
      </div>
    );
  }
  return (
    <div
      className={`inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-elevated max-w-[280px] ${onRetry ? "cursor-pointer hover:bg-hover transition-colors" : ""}`}
      title={originalPath || filename}
      onClick={onRetry ? handleRetry : undefined}
    >
      <FileIconSvg className="w-4 h-4 text-dim shrink-0" />
      <span className="text-xs text-dim truncate flex-1 min-w-0">{filename}</span>
      <span className="text-[10px] text-dim uppercase shrink-0 opacity-60">missing</span>
      {onRetry && (
        <button
          type="button"
          onClick={handleRetry}
          title="Check again"
          className="p-0.5 rounded hover:bg-hover transition-colors text-dim hover:text-label shrink-0"
        >
          <RefreshIconSvg className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
}
