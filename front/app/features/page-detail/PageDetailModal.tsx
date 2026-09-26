import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { Icon } from "@iconify/react";
import { AppOverlayPortal } from "@/components/AppOverlayPortal";
import { apiUrl } from "@/utils/api";
import { countOriginalTextRegions } from "@/utils/textRegions";
import type { EditableTextBlock, FinishedImage } from "@/types";
import { PageImageStage } from "@/features/page-detail/PageImageStage";
import { PageDetailHeader, type PageDetailViewMode } from "@/features/page-detail/PageDetailHeader";
import { ProfessionalLocalizationTab } from "@/features/page-detail/tabs/ProfessionalLocalizationTab";
import { StoryAnalysisTab } from "@/features/page-detail/tabs/StoryAnalysisTab";
import { StepSettingsTab } from "@/features/page-detail/tabs/StepSettingsTab";
import { TimingTab } from "@/features/page-detail/tabs/TimingTab";
import { PipelineDetailsSidebar, type SidebarTab } from "@/features/page-detail/PipelineDetailsSidebar";
import { usePageDetailArtifacts } from "@/features/page-detail/usePageDetailArtifacts";
import { usePageDetailActions } from "@/features/page-detail/usePageDetailActions";
import { usePageDetailSidebarResize } from "@/features/page-detail/usePageDetailSidebarResize";
import { resolvePipelineStepSettings } from "@/utils/pageDetailSettings";
import { resolveImageUrls, resolveTranslationTiming } from "@/utils/pageDetailTiming";
export {
  COLORIZER_LABELS,
  DETECTOR_LABELS,
  formatElapsedTime,
  INPAINTER_LABELS,
  OCR_LABELS,
  RENDERER_LABELS,
  resolveLanguageName,
  resolvePipelineStepSettings,
  resolveTranslatorEngine,
  resolveTranslatorModel,
  UPSCALER_LABELS,
} from "@/utils/pageDetailSettings";
export type { ResolvedPipelineStepSettings } from "@/utils/pageDetailSettings";
export {
  formatTimestamp,
  resolveImageUrls,
  resolveStagesToRetry,
  resolveTranslationTiming,
  shouldLoadTranslationArtifacts,
  timestampTooltip,
} from "@/utils/pageDetailTiming";
export type { ResolvedImageUrls } from "@/utils/pageDetailTiming";

export interface PageDetailModalProps {
  image: FinishedImage;
  onClose: () => void;
  // Optional navigation when opened from a manga page list
  images?: FinishedImage[];
  currentIndex?: number;
  onNavigate?: (index: number) => void;
  // Optional actions
  onDownload?: (image: FinishedImage) => void;
  onDelete?: (image: FinishedImage) => void;
  onEdit?: (image: FinishedImage) => void;
  onRetry?: (image: FinishedImage) => void | Promise<void>;
  onRetryFromStage?: (image: FinishedImage, stageId: string) => void | Promise<void>;
  onRerender?: (image: FinishedImage) => void | Promise<void>;
  titlePrefix?: string;
}

export const PageDetailModal: React.FC<PageDetailModalProps> = ({
  image,
  onClose,
  images = [],
  currentIndex,
  onNavigate,
  onDownload,
  onDelete,
  onEdit,
  onRetry,
  onRetryFromStage,
  onRerender,
  titlePrefix = "Page Detail",
}) => {
  const [zoomLevel, setZoomLevel] = useState(1);
  const [viewMode, setViewMode] = useState<PageDetailViewMode>("translated");
  const [showBubbleBoxes, setShowBubbleBoxes] = useState(false);
  const [showOriginalRegions, setShowOriginalRegions] = useState(false);
  const [isHoldingOriginal, setIsHoldingOriginal] = useState(false);
  const [copyLinkStatus, setCopyLinkStatus] = useState<"copied" | "error" | null>(null);
  const [isCompactViewport, setIsCompactViewport] = useState(() =>
    typeof window !== "undefined" && window.matchMedia("(max-width: 640px)").matches,
  );
  const [bubbleCount, setBubbleCount] = useState<number | null>(null);
  const [originalRegionCount, setOriginalRegionCount] = useState<number | null>(null);

  const [isRetrying, setIsRetrying] = useState(false);
  const [retryStatus, setRetryStatus] = useState<"queued" | "error" | null>(null);
  const [isRerendering, setIsRerendering] = useState(false);
  const [rerenderStatus, setRerenderStatus] = useState<"queued" | "error" | null>(null);

  // Sidebar tab and resize state
  const { sidebarWidth, modalContainerRef, handleResizeMouseDown } = usePageDetailSidebarResize();
  const isOriginal = image.sourceType === "original";

  useEffect(() => {
    setCopyLinkStatus(null);
  }, [image.id]);

  useEffect(() => {
    if (!copyLinkStatus) return;
    const timeout = window.setTimeout(() => setCopyLinkStatus(null), 1500);
    return () => window.clearTimeout(timeout);
  }, [copyLinkStatus, image.id]);

  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const attachCloseButton = useCallback((element: HTMLButtonElement | null) => {
    closeButtonRef.current = element;
    element?.focus();
  }, []);

  // Focus close button on mount
  useEffect(() => {
    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();
    return () => restoreFocusRef.current?.focus();
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 640px)");
    const update = () => setIsCompactViewport(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  // Reset stage-specific details when the active image changes
  useEffect(() => {
    setZoomLevel(1);
    setViewMode(image.sourceType === "original" ? "original" : "translated");
    setShowBubbleBoxes(false);
    setShowOriginalRegions(false);
    setIsHoldingOriginal(false);
    setBubbleCount(null);
    setOriginalRegionCount(null);
    setIsRetrying(false);
    setRetryStatus(null);
    setIsRerendering(false);
    setRerenderStatus(null);
  }, [image.id, image.folder, image.sourceType]);

  const resolvedImageUrls = useMemo(() => resolveImageUrls(image), [image]);
  const {
    folder: resolvedFolder,
    resultUrl: resolvedResultUrl,
    resultPreviewUrl: resolvedResultPreviewUrl,
    resultPlaceholderUrl: resolvedResultPlaceholderUrl,
    inpaintedUrl: resolvedInpaintedUrl,
    inpaintedPreviewUrl: resolvedInpaintedPreviewUrl,
    bubbleMaskUrl,
    originalUrl: resolvedOriginalUrl,
    originalPreviewUrl: resolvedOriginalPreviewUrl,
  } = resolvedImageUrls;
  const {
    discoveredBubbleMaskUrl,
    pipelineManifest,
    isPipelineTimingLoading,
    translationDetail,
    professionalAudit,
    isTranslationDetailLoading,
  } = usePageDetailArtifacts(resolvedFolder, image.sourceType, isOriginal, bubbleMaskUrl);
  const resolvedBubbleMaskUrl = bubbleMaskUrl ?? discoveredBubbleMaskUrl;

  const {
    activeIndex,
    hasPrev,
    hasNext,
    handleDownload,
    handleCopyLink,
    handleRetry,
    queueRetryFromStage,
    handleRerender,
    handlePrev,
    handleNext,
  } = usePageDetailActions({
    image,
    viewMode,
    isOriginal,
    resolvedImageUrls,
    images,
    currentIndex,
    onClose,
    onNavigate,
    onDownload,
    onRetry,
    onRetryFromStage,
    onRerender,
    isRetrying,
    setIsRetrying,
    retryStatus,
    setRetryStatus,
    isRerendering,
    setIsRerendering,
    rerenderStatus,
    setRerenderStatus,
    setCopyLinkStatus,
    setZoomLevel,
  });

  const stepSettings = useMemo(
    () => resolvePipelineStepSettings(image.settings, pipelineManifest, translationDetail),
    [image.settings, pipelineManifest, translationDetail],
  );
  const engine = stepSettings.translation.engine;
  const model = stepSettings.translation.model;
  const timing = resolveTranslationTiming(pipelineManifest, image.finishedAt, image.startedAt, image.durationMs);
  const originalTextAvailable = Boolean(
    image.hasTextRegions || image.textRegionsUrl || (bubbleCount !== null && bubbleCount > 0),
  );
  const handleTextRegionsLoaded = useCallback((blocks: EditableTextBlock[]) => {
    setBubbleCount(blocks.length);
    setOriginalRegionCount(countOriginalTextRegions(blocks));
  }, []);

  return (
    <AppOverlayPortal>
    <div
      ref={modalContainerRef}
      data-app-overlay="page-detail"
      style={{ "--details-sidebar-width": `${sidebarWidth}px` } as React.CSSProperties}
      className="fixed inset-0 z-50 flex flex-col bg-black/90 p-2 sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="page-detail-viewer-title"
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-white/10 bg-zinc-950/95 shadow-2xl">
        {/* Top Header Bar */}
        <PageDetailHeader
          image={image}
          titlePrefix={titlePrefix}
          zoomLevel={zoomLevel}
          setZoomLevel={setZoomLevel}
          viewMode={viewMode}
          setViewMode={setViewMode}
          isOriginal={isOriginal}
          originalTextAvailable={originalTextAvailable}
          resolvedOriginalUrl={resolvedOriginalUrl}
          resolvedResultUrl={resolvedResultUrl}
          resolvedInpaintedUrl={resolvedInpaintedUrl}
          resolvedBubbleMaskUrl={resolvedBubbleMaskUrl}
          setShowBubbleBoxes={setShowBubbleBoxes}
          showBubbleBoxes={showBubbleBoxes}
          bubbleCount={bubbleCount}
          setShowOriginalRegions={setShowOriginalRegions}
          showOriginalRegions={showOriginalRegions}
          originalRegionCount={originalRegionCount}
          setIsHoldingOriginal={setIsHoldingOriginal}
          onEdit={onEdit}
          onRetry={onRetry}
          handleRetry={handleRetry}
          isRetrying={isRetrying}
          retryStatus={retryStatus}
          onRerender={onRerender}
          handleRerender={handleRerender}
          isRerendering={isRerendering}
          rerenderStatus={rerenderStatus}
          handleCopyLink={handleCopyLink}
          copyLinkStatus={copyLinkStatus}
          handleDownload={handleDownload}
          onDelete={onDelete}
          attachCloseButton={attachCloseButton}
          onClose={onClose}
        />
        {/* Modal Main Viewport */}
        {/* Standard Image Preview Viewport */}
          <div className="relative min-h-0 flex-1 overflow-hidden p-2 sm:p-5">
            <div className="flex h-full min-h-0 flex-col gap-3 lg:flex-row">
              <div className="relative min-h-0 min-w-0 flex-1 overflow-auto">
            {/* Previous Page Chevron Button */}
            {images.length > 1 && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  handlePrev();
                }}
                className="absolute left-4 top-1/2 -translate-y-1/2 z-30 flex h-11 w-11 items-center justify-center rounded-full bg-black/70 text-white shadow-xl backdrop-blur-xs transition-all hover:scale-105 hover:bg-indigo-600 focus-visible:outline-2 focus-visible:outline-indigo-400 cursor-pointer"
                aria-label="Previous page (←)"
                title="Previous page (←)"
              >
                <Icon icon="carbon:chevron-left" className="h-6 w-6" />
              </button>
            )}

            {/* Next Page Chevron Button */}
            {images.length > 1 && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  handleNext();
                }}
                className="absolute right-4 top-1/2 -translate-y-1/2 z-30 flex h-11 w-11 items-center justify-center rounded-full bg-black/70 text-white shadow-xl backdrop-blur-xs transition-all hover:scale-105 hover:bg-indigo-600 focus-visible:outline-2 focus-visible:outline-indigo-400 cursor-pointer"
                aria-label="Next page (→)"
                title="Next page (→)"
              >
                <Icon icon="carbon:chevron-right" className="h-6 w-6" />
              </button>
            )}

            <div
              className="flex min-h-full min-w-full items-center justify-center"
              onClick={(event) => event.stopPropagation()}
            >
              <PageImageStage
                imageId={image.id}
                zoomLevel={zoomLevel}
                isOriginal={isOriginal}
                originalUrl={resolvedOriginalPreviewUrl}
                originalFullUrl={resolvedOriginalUrl}
                resultUrl={isCompactViewport && image.detailPreviewUrl
                  ? apiUrl(image.detailPreviewUrl)
                  : resolvedResultPreviewUrl}
                resultPlaceholder={resolvedResultPlaceholderUrl}
                resultFullUrl={resolvedResultUrl}
                inpaintedUrl={resolvedInpaintedPreviewUrl}
                inpaintedFullUrl={resolvedInpaintedUrl}
                coordinateSize={pipelineManifest?.source?.width && pipelineManifest.source.height
                  ? { width: pipelineManifest.source.width, height: pipelineManifest.source.height }
                  : null}
                useFullResolution={zoomLevel > 1.5}
                folder={resolvedFolder}
                textRegionsUrl={image.textRegionsUrl}
                viewMode={viewMode}
                onViewModeChange={setViewMode}
                showBubbleBoxes={showBubbleBoxes}
                showBubbleRegions={viewMode === "speech-bubbles"}
                showOriginalRegions={showOriginalRegions}
                onToggleBubbleBoxes={setShowBubbleBoxes}
                isHoldingOriginal={isHoldingOriginal}
                onTextRegionsLoaded={handleTextRegionsLoaded}
                showComparisonControls={isOriginal ? originalTextAvailable : true}
              />
            </div>
              </div>

              {!isOriginal && (
                <div className="relative hidden lg:flex shrink-0 flex-col" style={{ width: "var(--details-sidebar-width)" }}>
                  {/* Drag-to-resize handle */}
                  <div
                    className="absolute left-0 top-0 h-full w-1 cursor-col-resize z-10 group"
                    onMouseDown={handleResizeMouseDown}
                    title="Drag to resize"
                  >
                    <div className="h-full w-px bg-white/10 group-hover:bg-indigo-400/60 transition-colors" />
                  </div>

                  <aside
                    aria-label="Pipeline details"
                    className="flex h-full flex-col rounded-xl border border-white/10 bg-white/5 text-white overflow-hidden"
                    onClick={(event) => event.stopPropagation()}
                  >
                    <PipelineDetailsSidebar imageId={image.id} render={(sidebar) => {
                      return (
                      <>
                    {/* Tab bar */}
                    <div className="flex shrink-0 border-b border-white/10 bg-black/20">
                      {(
                        [
                          { id: "timing", label: "Pipeline Timing", icon: "carbon:time" },
                          { id: "localization", label: "Pro Localization", icon: "carbon:translate" },
                          { id: "story", label: "Story Analysis", icon: "carbon:document" },
                          { id: "settings", label: "Step Settings", icon: "carbon:tune" },
                        ] as { id: SidebarTab; label: string; icon: string }[]
                      ).map((tab) => (
                        <button
                          key={tab.id}
                          type="button"
                          onClick={() => sidebar.setTab(tab.id)}
                          className={`flex flex-1 flex-col items-center gap-1 px-1 py-2 text-[10px] font-medium transition-colors cursor-pointer ${
                            sidebar.tab === tab.id
                              ? "border-b-2 border-indigo-400 text-indigo-300"
                              : "border-b-2 border-transparent text-zinc-400 hover:text-zinc-200"
                          }`}
                          title={tab.label}
                        >
                          <Icon icon={tab.icon} className="h-3.5 w-3.5" />
                          <span className="leading-tight text-center">{tab.label}</span>
                        </button>
                      ))}
                    </div>

                    {/* Tab content */}
                    <div className="flex-1 overflow-y-auto p-4">

                      <TimingTab
                        timing={timing}
                        pipelineManifest={pipelineManifest}
                        isPipelineTimingLoading={isPipelineTimingLoading}
                        isTranslationDetailLoading={isTranslationDetailLoading}
                        canRetryFromStage={Boolean(onRetryFromStage)}
                        sidebar={sidebar}
                        onQueueRetryFromStage={queueRetryFromStage}
                      />

                      {/* ── Professional Localization tab ── */}
                      {sidebar.tab === "localization" && <ProfessionalLocalizationTab professionalAudit={professionalAudit} />}

                      {/* ── Story Analysis tab ── */}
                      {sidebar.tab === "story" && <StoryAnalysisTab professionalAudit={professionalAudit} />}

                      {/* ── Step Settings tab ── */}
                      {sidebar.tab === "settings" && <StepSettingsTab stepSettings={stepSettings} engine={engine} model={model} isTranslationDetailLoading={isTranslationDetailLoading} />}

                    </div>
                      </>
                      );
                    }} />
                  </aside>
                </div>
              )}

            </div>
          </div>

        {/* Bottom Info & Navigation Bar */}
        <div
          className="flex flex-wrap items-center justify-between gap-2 border-t border-white/10 bg-black/50 px-4 py-2.5 text-xs text-zinc-400"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center space-x-3">
            {images.length > 0 && activeIndex !== -1 && (
              <span className="font-medium text-zinc-200">
                Page {activeIndex + 1} of {images.length}
              </span>
            )}
            {image.mangaTitle && (
              <span className="text-zinc-400 font-medium">
                • {image.mangaTitle}
              </span>
            )}
            {!isOriginal && (
              <span className="hidden md:inline">
                Engine: {image.settings?.translator || "Default"}
              </span>
            )}
          </div>

          <div className="hidden sm:flex items-center space-x-2 text-[11px] font-mono text-zinc-500">
            {images.length > 1 && (
              <>
                <span>Use ← → to navigate</span>
                <span>•</span>
              </>
            )}
            <span>+/- to zoom</span>
            <span>•</span>
            <span>Esc to close</span>
          </div>
        </div>
      </div>
    </div>
    </AppOverlayPortal>
  );
};

export default PageDetailModal;
