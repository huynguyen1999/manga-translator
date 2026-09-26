import React from "react";
import type { Dispatch, SetStateAction } from "react";
import { Icon } from "@iconify/react";
import { Link } from "react-router";
import type { FinishedImage } from "@/types";
import { buildMangaDetailIdUrl, buildMangaDetailUrl } from "@/utils/routeState";

export type PageDetailViewMode = "split" | "translated" | "inpainted" | "original" | "speech-bubbles";

interface PageDetailHeaderProps {
  image: FinishedImage;
  titlePrefix: string;
  zoomLevel: number;
  setZoomLevel: Dispatch<SetStateAction<number>>;
  viewMode: PageDetailViewMode;
  setViewMode: Dispatch<SetStateAction<PageDetailViewMode>>;
  isOriginal: boolean;
  originalTextAvailable: boolean;
  resolvedOriginalUrl: Blob | File | string | null;
  resolvedResultUrl: Blob | string | null;
  resolvedInpaintedUrl: string | null;
  resolvedBubbleMaskUrl: string | null;
  setShowBubbleBoxes: Dispatch<SetStateAction<boolean>>;
  showBubbleBoxes: boolean;
  bubbleCount: number | null;
  setShowOriginalRegions: Dispatch<SetStateAction<boolean>>;
  showOriginalRegions: boolean;
  originalRegionCount: number | null;
  setIsHoldingOriginal: Dispatch<SetStateAction<boolean>>;
  onEdit?: (image: FinishedImage) => void;
  onRetry?: (image: FinishedImage) => void | Promise<void>;
  handleRetry: () => Promise<void>;
  isRetrying: boolean;
  retryStatus: "queued" | "error" | null;
  onRerender?: (image: FinishedImage) => void | Promise<void>;
  handleRerender: () => Promise<void>;
  isRerendering: boolean;
  rerenderStatus: "queued" | "error" | null;
  handleCopyLink: () => Promise<void>;
  copyLinkStatus: "copied" | "error" | null;
  handleDownload: () => void;
  onDelete?: (image: FinishedImage) => void;
  attachCloseButton: (element: HTMLButtonElement | null) => void;
  onClose: () => void;
}

export const PageDetailHeader: React.FC<PageDetailHeaderProps> = ({
  image,
  titlePrefix,
  zoomLevel,
  setZoomLevel,
  viewMode,
  setViewMode,
  isOriginal,
  originalTextAvailable,
  resolvedOriginalUrl,
  resolvedResultUrl,
  resolvedInpaintedUrl,
  resolvedBubbleMaskUrl,
  setShowBubbleBoxes,
  showBubbleBoxes,
  bubbleCount,
  setShowOriginalRegions,
  showOriginalRegions,
  originalRegionCount,
  setIsHoldingOriginal,
  onEdit,
  onRetry,
  handleRetry,
  isRetrying,
  retryStatus,
  onRerender,
  handleRerender,
  isRerendering,
  rerenderStatus,
  handleCopyLink,
  copyLinkStatus,
  handleDownload,
  onDelete,
  attachCloseButton,
  onClose,
}) => {
  const pageTitle = (image.originalName && image.originalName !== "Unknown")
    ? image.originalName
    : (image.folder ? `${image.folder}.png` : "Manga Page");
  const baseName = pageTitle.replace(/\.[^/.]+$/, "").trim().toLowerCase();
  const mangaTitleClean = image.mangaTitle?.trim() || "";
  const isDistinctMangaTitle = Boolean(
    mangaTitleClean &&
    mangaTitleClean.toLowerCase() !== "ungrouped" &&
    mangaTitleClean.toLowerCase() !== baseName
  );
  const mangaLink = image.groupId
    ? buildMangaDetailIdUrl(image.groupId)
    : image.mangaTitle
    ? buildMangaDetailUrl(image.mangaTitle)
    : null;

  return (
    <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-white/10 bg-black/40 px-3 py-2 text-white sm:px-4">
      <div className="min-w-0 flex items-center gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-xs text-zinc-400">
            {isDistinctMangaTitle && mangaLink ? (
              <Link
                to={mangaLink}
                className="font-medium text-zinc-300 hover:text-indigo-400 truncate max-w-[200px] transition-colors"
                title={`Open manga "${image.mangaTitle}"`}
              >
                {image.mangaTitle}
              </Link>
            ) : isDistinctMangaTitle ? (
              <span className="font-medium text-zinc-300 truncate max-w-[200px]">
                {image.mangaTitle}
              </span>
            ) : null}
            {isDistinctMangaTitle && <span className="text-zinc-600">/</span>}
            <span className="text-[11px] text-zinc-400 font-medium">
              {titlePrefix}
            </span>
          </div>
          <h2 id="page-detail-viewer-title" className="truncate text-sm font-semibold text-zinc-100 sm:text-base">
            {pageTitle}
          </h2>
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-1.5">
            {/* Zoom Controls */}
            <div className="flex shrink-0 items-center rounded-md bg-white/10 p-0.5" aria-label="Zoom controls">
                <button
                  type="button"
                  onClick={() => setZoomLevel((level) => Math.max(0.5, level - 0.25))}
                  className="flex min-h-8 min-w-8 items-center justify-center rounded text-zinc-200 transition-colors hover:bg-white/15 hover:text-white focus-visible:outline-2 focus-visible:outline-indigo-400 cursor-pointer"
                  aria-label="Zoom out (-)"
                  title="Zoom out (-)"
                >
                  <Icon icon="carbon:zoom-out" className="h-4 w-4" />
                </button>
                <span className="min-w-10 px-0.5 text-center font-mono text-xs text-zinc-300" aria-live="polite">
                  {Math.round(zoomLevel * 100)}%
                </span>
                <button
                  type="button"
                  onClick={() => setZoomLevel((level) => Math.min(3, level + 0.25))}
                  className="flex min-h-8 min-w-8 items-center justify-center rounded text-zinc-200 transition-colors hover:bg-white/15 hover:text-white focus-visible:outline-2 focus-visible:outline-indigo-400 cursor-pointer"
                  aria-label="Zoom in (+)"
                  title="Zoom in (+)"
                >
                  <Icon icon="carbon:zoom-in" className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setZoomLevel(1)}
                  className="flex min-h-8 min-w-8 items-center justify-center rounded text-zinc-200 transition-colors hover:bg-white/15 hover:text-white focus-visible:outline-2 focus-visible:outline-indigo-400 cursor-pointer"
                  aria-label="Reset zoom (0)"
                  title="Reset zoom (0)"
                >
                  <Icon icon="carbon:zoom-reset" className="h-4 w-4" />
                </button>
            </div>

            {/* View Mode and Inspection Controls */}
          {(isOriginal ? originalTextAvailable : true) && (
          <div className="flex min-w-0 flex-wrap items-center rounded-md border border-white/10 bg-white/5 p-0.5" role="tablist">
            {!isOriginal && (
            <>
              {resolvedOriginalUrl && resolvedResultUrl && (
                <button
                  type="button"
                  onClick={() => setViewMode("split")}
                  className={`flex min-h-8 items-center gap-1 rounded px-2 text-xs font-semibold transition-colors cursor-pointer ${
                    viewMode === "split"
                      ? "bg-indigo-600 text-white shadow-xs"
                      : "text-zinc-300 hover:bg-white/10 hover:text-white"
                  }`}
                  title="Split comparison slider"
                >
                  Split
                </button>
              )}

              {resolvedResultUrl && (
                <button
                  type="button"
                  onClick={() => setViewMode("translated")}
                  className={`flex min-h-8 items-center gap-1 rounded px-2 text-xs font-semibold transition-colors cursor-pointer ${
                    viewMode === "translated"
                      ? "bg-indigo-600 text-white shadow-xs"
                      : "text-zinc-300 hover:bg-white/10 hover:text-white"
                  }`}
                  title="Show translated image"
                >
                  Translated
                </button>
              )}

              {resolvedInpaintedUrl && (
                <button
                  type="button"
                  onClick={() => setViewMode("inpainted")}
                  className={`flex min-h-8 items-center gap-1 rounded px-2 text-xs font-semibold transition-colors cursor-pointer ${
                    viewMode === "inpainted"
                      ? "bg-emerald-600 text-white shadow-xs"
                      : "text-zinc-300 hover:bg-white/10 hover:text-white"
                  }`}
                  title="Show inpainted (clean artwork)"
                >
                  Inpainted
                </button>
              )}

              {resolvedBubbleMaskUrl && (
                <button
                  type="button"
                  onClick={() => {
                    setShowBubbleBoxes(false);
                    setShowOriginalRegions(false);
                    setViewMode("speech-bubbles");
                  }}
                  className={`flex min-h-8 items-center gap-1 rounded px-2 text-xs font-semibold transition-colors cursor-pointer ${
                    viewMode === "speech-bubbles"
                      ? "bg-violet-600 text-white shadow-xs"
                      : "text-zinc-300 hover:bg-white/10 hover:text-white"
                  }`}
                  title="Show saved speech bubble regions over the original page"
                >
                  Speech bubbles
                </button>
              )}

              {resolvedOriginalUrl && (
                <button
                  type="button"
                  onClick={() => setViewMode("original")}
                  className={`flex min-h-8 items-center gap-1 rounded px-2 text-xs font-semibold transition-colors cursor-pointer ${
                    viewMode === "original"
                      ? "bg-indigo-600 text-white shadow-xs"
                      : "text-zinc-300 hover:bg-white/10 hover:text-white"
                  }`}
                  title="Show original input image"
                >
                  Original
                </button>
              )}

              {resolvedOriginalUrl && (
                <button
                  type="button"
                  onMouseDown={() => setIsHoldingOriginal(true)}
                  onMouseUp={() => setIsHoldingOriginal(false)}
                  onMouseLeave={() => setIsHoldingOriginal(false)}
                  onTouchStart={() => setIsHoldingOriginal(true)}
                  onTouchEnd={() => setIsHoldingOriginal(false)}
                  className="flex min-h-8 select-none items-center gap-1 rounded bg-zinc-800/80 px-2 text-xs font-semibold text-amber-300 transition-colors hover:bg-zinc-700 active:bg-amber-500 active:text-black cursor-pointer"
                  title="Hold button to peek at original image"
                >
                  Hold Peek
                </button>
              )}
            </>
            )}

              <button
                type="button"
                onClick={() => {
                  setShowBubbleBoxes((prev) => !prev);
                }}
                className={`flex min-h-8 items-center gap-1 rounded px-2 text-xs font-semibold transition-colors cursor-pointer ${
                  showBubbleBoxes
                    ? "bg-amber-600 text-white shadow-xs"
                    : "text-zinc-300 hover:bg-white/10 hover:text-white"
                }`}
                title={isOriginal
                  ? (showBubbleBoxes ? "Hide detected text" : "Show detected text")
                  : (showBubbleBoxes ? "Hide speech bubble boxes" : "Show speech bubble boxes")}
              >
                <Icon icon="carbon:chat" className="h-3.5 w-3.5" />
                <span>{isOriginal ? "Detected text" : "Bubbles"}</span>
                {bubbleCount !== null && bubbleCount > 0 && (
                  <span
                    className={`rounded-full px-1.5 py-0.2 text-[10px] ${
                      showBubbleBoxes ? "bg-amber-700/80 text-white" : "bg-zinc-800 text-zinc-300"
                    }`}
                  >
                    {bubbleCount}
                  </span>
                )}
              </button>

              <button
                type="button"
                disabled={!originalRegionCount}
                onClick={() => setShowOriginalRegions((prev) => !prev)}
                className={`flex min-h-8 items-center gap-1 rounded px-2 text-xs font-semibold transition-colors cursor-pointer disabled:cursor-not-allowed disabled:opacity-50 ${
                  showOriginalRegions
                    ? "bg-amber-600 text-white shadow-xs"
                    : "text-zinc-300 hover:bg-white/10 hover:text-white"
                }`}
                title={
                  originalRegionCount === null
                    ? "Loading original detector regions…"
                    : originalRegionCount > 0
                    ? showOriginalRegions
                      ? "Hide original detector regions"
                      : "Show original detector regions"
                    : "Original detector regions are not available for this result"
                }
                aria-pressed={showOriginalRegions}
              >
                <Icon icon="carbon:scan" className="h-3.5 w-3.5" />
                <span>Original regions</span>
                {originalRegionCount !== null && originalRegionCount > 0 && (
                  <span
                    className={`rounded-full px-1.5 py-0.2 text-[10px] ${
                      showOriginalRegions ? "bg-amber-700/80 text-white" : "bg-zinc-800 text-zinc-300"
                    }`}
                  >
                    {originalRegionCount}
                  </span>
                )}
              </button>

            </div>
          )}

            <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-l border-white/10 pl-1.5">
            {/* Edit / Typeset Button */}
            {onEdit && image.hasTextRegions && image.sourceType !== "original" && (
              <button
                type="button"
                onClick={() => onEdit(image)}
                className="flex min-h-8 items-center gap-1 rounded-md border border-indigo-500/40 bg-indigo-500/20 px-2.5 text-xs font-semibold text-indigo-200 transition-colors hover:bg-indigo-600 hover:text-white focus-visible:outline-2 focus-visible:outline-indigo-300 cursor-pointer"
                title="Interactive Typesetter & Visual Editor"
              >
                <Icon icon="carbon:text-annotation-toggle" className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Edit / Typeset</span>
              </button>
            )}

            {onRetry && image.sourceType !== "original" && (
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => void handleRetry()}
                  disabled={isRetrying || retryStatus === "queued"}
                  className="flex min-h-8 items-center gap-1 rounded-md border border-amber-500/40 bg-amber-500/15 px-2.5 text-xs font-semibold text-amber-200 transition-colors hover:bg-amber-500/25 hover:text-white focus-visible:outline-2 focus-visible:outline-amber-300 disabled:cursor-wait disabled:opacity-70"
                  aria-busy={isRetrying}
                  title="Run the translation pipeline again for this image"
                >
                  <Icon icon="carbon:renew" className={`h-3.5 w-3.5 ${isRetrying ? "animate-spin" : ""}`} />
                  <span className="hidden sm:inline">
                    {isRetrying ? "Retrying…" : retryStatus === "queued" ? "Retry queued" : "Retry pipeline"}
                  </span>
                </button>
                {retryStatus === "error" && (
                  <span className="text-xs font-medium text-rose-300" role="status">
                    Retry failed
                  </span>
                )}
              </div>
            )}

            {onRerender && image.sourceType !== "original" && (
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => void handleRerender()}
                  disabled={isRerendering || rerenderStatus === "queued"}
                  className="flex min-h-8 items-center gap-1 rounded-md border border-indigo-500/40 bg-indigo-500/15 px-2.5 text-xs font-semibold text-indigo-200 transition-colors hover:bg-indigo-500/25 hover:text-white focus-visible:outline-2 focus-visible:outline-indigo-300 disabled:cursor-wait disabled:opacity-70"
                  aria-busy={isRerendering}
                  title="Rerun pipeline stages (typesetting, retranslation, reprocess text, or full)"
                >
                  <Icon icon="carbon:reset" className={`h-3.5 w-3.5 ${isRerendering ? "animate-spin" : ""}`} />
                  <span className="hidden sm:inline">
                    {isRerendering ? "Rerunning…" : rerenderStatus === "queued" ? "Rerun queued" : "Rerun pipeline"}
                  </span>
                </button>
                {rerenderStatus === "error" && (
                  <span className="text-xs font-medium text-rose-300" role="status">
                    Rerun failed
                  </span>
                )}
              </div>
            )}

            {/* Download Button */}
            {image.folder && (
              <button
                type="button"
                onClick={() => void handleCopyLink()}
                className="flex min-h-8 items-center gap-1 rounded-md border border-white/15 bg-white/5 px-2.5 text-xs font-semibold text-zinc-200 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline-2 focus-visible:outline-indigo-300 cursor-pointer"
                aria-label={copyLinkStatus === "copied" ? "Page link copied" : copyLinkStatus === "error" ? "Could not copy page link" : "Copy page link"}
                title="Copy link to this page"
              >
                <Icon icon={copyLinkStatus === "copied" ? "carbon:checkmark" : "carbon:link"} className="h-3.5 w-3.5" />
                <span className="hidden sm:inline" aria-live="polite">
                  {copyLinkStatus === "copied" ? "Copied" : copyLinkStatus === "error" ? "Copy failed" : "Copy link"}
                </span>
              </button>
            )}
            <button
              type="button"
              onClick={handleDownload}
              className="flex min-h-8 items-center gap-1 rounded-md bg-indigo-600 px-2.5 text-xs font-semibold text-white transition-colors hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-indigo-300 cursor-pointer"
              aria-label="Download image"
              title="Download image"
            >
              <Icon icon="carbon:download" className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Download</span>
            </button>

            {/* Delete Button */}
            {onDelete && (
              <button
                type="button"
                onClick={() => onDelete(image)}
                className="flex min-h-8 items-center gap-1 rounded-md bg-zinc-800 hover:bg-red-600 px-2.5 text-xs font-semibold text-white hover:text-white transition-colors cursor-pointer"
                title="Delete from server"
              >
                <Icon icon="carbon:trash-can" className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Delete</span>
              </button>
            )}

            {/* Close Button */}
            <button
              ref={attachCloseButton}
              type="button"
              onClick={onClose}
              className="flex min-h-8 min-w-8 items-center justify-center rounded-md text-zinc-300 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline-2 focus-visible:outline-indigo-300 cursor-pointer"
              aria-label="Close viewer (Escape)"
              title="Close viewer (Escape)"
            >
              <Icon icon="carbon:close" className="h-5 w-5" />
            </button>
            </div>
          </div>
        </header>
  );
};
