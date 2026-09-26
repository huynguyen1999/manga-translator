import React from "react";
import { PreviewImage } from "@/components/PreviewImage";
import { RenderProfiler } from "@/utils/renderPerformance";
import type { EditableTextBlock } from "@/types";

interface PageImageStageProps {
  imageId: string;
  zoomLevel: number;
  isOriginal: boolean;
  originalUrl: Blob | File | string | null;
  originalFullUrl: Blob | File | string | null;
  resultFullUrl: Blob | string | null;
  inpaintedFullUrl: string | null;
  coordinateSize?: { width: number; height: number } | null;
  useFullResolution: boolean;
  resultUrl: Blob | string | null;
  resultPlaceholder?: string | null;
  inpaintedUrl: string | null;
  folder: string | null;
  textRegionsUrl?: string | null;
  viewMode: "split" | "translated" | "inpainted" | "original" | "speech-bubbles";
  onViewModeChange: (mode: "split" | "translated" | "inpainted" | "original") => void;
  showBubbleBoxes: boolean;
  showBubbleRegions: boolean;
  showOriginalRegions: boolean;
  onToggleBubbleBoxes: (show: boolean) => void;
  isHoldingOriginal: boolean;
  onTextRegionsLoaded: (blocks: EditableTextBlock[]) => void;
  showComparisonControls: boolean;
}

export const PageImageStage = React.memo<PageImageStageProps>(({
  imageId, zoomLevel, isOriginal, originalUrl, originalFullUrl, resultUrl, resultPlaceholder,
  resultFullUrl, inpaintedUrl, inpaintedFullUrl, coordinateSize, useFullResolution, folder,
  textRegionsUrl, viewMode, onViewModeChange, showBubbleBoxes, showBubbleRegions, showOriginalRegions,
  onToggleBubbleBoxes, isHoldingOriginal, onTextRegionsLoaded, showComparisonControls,
}) => (
  <RenderProfiler id="PageDetailImageStage">
    <div
      className="relative flex h-[calc(100vh-12rem)] w-full max-w-6xl items-center justify-center transition-transform duration-100 sm:h-[calc(100vh-13rem)]"
      style={{ transform: `scale(${zoomLevel})` }}
    >
      <PreviewImage
        key={imageId}
        file={originalUrl}
        result={isOriginal ? null : resultUrl}
        resultPlaceholder={isOriginal ? null : resultPlaceholder}
        inpainted={isOriginal ? null : inpaintedUrl}
        fullOriginal={typeof originalFullUrl === "string" ? originalFullUrl : null}
        fullResult={typeof resultFullUrl === "string" ? resultFullUrl : null}
        fullInpainted={inpaintedFullUrl}
        coordinateSize={coordinateSize}
        useFullResolution={useFullResolution}
        folder={folder}
        textRegionsUrl={textRegionsUrl}
        viewMode={isOriginal ? "original" : viewMode === "speech-bubbles" ? "original" : viewMode}
        onViewModeChange={isOriginal ? undefined : onViewModeChange}
        showBubbleBoxes={showBubbleBoxes}
        showBubbleRegions={showBubbleRegions}
        showOriginalRegions={showOriginalRegions}
        onToggleBubbleBoxes={onToggleBubbleBoxes}
        isHoldingOriginal={isOriginal ? false : isHoldingOriginal}
        showFloatingToolbar={false}
        onTextRegionsLoaded={onTextRegionsLoaded}
        showComparisonControls={showComparisonControls}
        preloadImages={false}
        className="max-h-full max-w-full rounded-lg shadow-2xl"
      />
    </div>
  </RenderProfiler>
));
