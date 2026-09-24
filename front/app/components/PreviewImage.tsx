import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { Icon } from "@iconify/react";
import { apiUrl } from "@/utils/api";
import { countOriginalTextRegions, parseBubbleDetections, parseDetectionRegions, parseTextRegions, type DetectedBubbleRegion, type DetectedRegionLine } from "@/utils/textRegions";
import type { EditableTextBlock } from "@/types";

interface PreviewImageProps {
  file: Blob | string | null;
  result: Blob | File | string | null;
  inpainted?: Blob | File | string | null;
  folder?: string | null;
  textRegionsUrl?: string | null;
  textRegions?: EditableTextBlock[] | null;
  showBubbleBoxes?: boolean;
  showBubbleRegions?: boolean;
  showOriginalRegions?: boolean;
  onToggleBubbleBoxes?: (show: boolean) => void;
  isHoldingOriginal?: boolean;
  onHoldOriginalChange?: (holding: boolean) => void;
  showFloatingToolbar?: boolean;
  floatingToolbarPlacement?: "over-image" | "below-image";
  onTextRegionsLoaded?: (blocks: EditableTextBlock[]) => void;
  onOriginalRegionCountLoaded?: (count: number) => void;
  className?: string;
  showComparisonControls?: boolean;
  resultLabel?: string;
  originalLabel?: string;
  inpaintedLabel?: string;
  viewMode?: "split" | "translated" | "inpainted" | "original";
  onViewModeChange?: (mode: "split" | "translated" | "inpainted" | "original") => void;
  loading?: "lazy" | "eager";
  preloadImages?: boolean;
  fullOriginal?: string | null;
  fullResult?: string | null;
  fullInpainted?: string | null;
  coordinateSize?: { width: number; height: number } | null;
  useFullResolution?: boolean;
}

export function getConfidenceStyle(conf: number | null | undefined) {
  if (typeof conf !== "number" || isNaN(conf)) {
    return {
      stroke: "#f59e0b",
      fill: "#f59e0b",
      dot: "bg-amber-400",
      tier: "detected",
    };
  }
  if (conf >= 0.85) {
    return {
      stroke: "#10b981", // High (Emerald)
      fill: "#10b981",
      dot: "bg-emerald-400",
      tier: "high",
    };
  }
  if (conf >= 0.65) {
    return {
      stroke: "#f59e0b", // Medium (Amber)
      fill: "#f59e0b",
      dot: "bg-amber-400",
      tier: "medium",
    };
  }
  return {
    stroke: "#ef4444", // Low (Rose)
    fill: "#ef4444",
    dot: "bg-rose-400",
    tier: "low",
  };
}

export const PreviewImage: React.FC<PreviewImageProps> = React.memo(
  ({
    file,
    result,
    inpainted,
    folder,
    textRegionsUrl,
    textRegions,
    showBubbleBoxes: controlledShowBubbleBoxes,
    showBubbleRegions = false,
    showOriginalRegions = false,
    onToggleBubbleBoxes,
    isHoldingOriginal: controlledIsHoldingOriginal,
    onHoldOriginalChange,
    showFloatingToolbar = true,
    floatingToolbarPlacement = "over-image",
    onTextRegionsLoaded,
    onOriginalRegionCountLoaded,
    className = "",
    showComparisonControls = true,
    resultLabel = "Translated",
    originalLabel = "Original",
    inpaintedLabel = "Inpainted",
    viewMode: controlledViewMode,
    onViewModeChange,
    loading,
    preloadImages = true,
    fullOriginal,
    fullResult,
    fullInpainted,
    coordinateSize,
    useFullResolution = false,
  }) => {
    const [originalUrl, setOriginalUrl] = useState<string | null>(null);
    const [resultUrl, setResultUrl] = useState<string | null>(null);
    const [inpaintedUrl, setInpaintedUrl] = useState<string | null>(null);
    const [sliderPos, setSliderPos] = useState(50); // percentage 0 - 100
    const [internalViewMode, setInternalViewMode] = useState<"split" | "translated" | "inpainted" | "original">("translated");
    const [internalIsHoldingOriginal, setInternalIsHoldingOriginal] = useState(false);
    const [resultLoadFailed, setResultLoadFailed] = useState(false);
    const [resultLoaded, setResultLoaded] = useState(false);
    const [retryCount, setRetryCount] = useState(0);
    const [previewFallbacks, setPreviewFallbacks] = useState({ original: false, result: false, inpainted: false });

    const isHoldingOriginal = controlledIsHoldingOriginal ?? internalIsHoldingOriginal;
    const setIsHoldingOriginal = (holding: boolean) => {
      setInternalIsHoldingOriginal(holding);
      onHoldOriginalChange?.(holding);
    };

    // Bubble detection & text inspection state
    const [internalTextRegions, setInternalTextRegions] = useState<EditableTextBlock[]>([]);
    const [detectedTextLines, setDetectedTextLines] = useState<DetectedRegionLine[] | null>(null);
    const [detectedBubbleRegions, setDetectedBubbleRegions] = useState<DetectedBubbleRegion[]>([]);
    const [bubbleRegionsStatus, setBubbleRegionsStatus] = useState<"idle" | "loading" | "loaded" | "error">("idle");
    const [isLoadingRegions, setIsLoadingRegions] = useState(false);
    const [internalShowBubbleBoxes, setInternalShowBubbleBoxes] = useState(false);
    const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);
    const [hoveredRegionIndex, setHoveredRegionIndex] = useState<number | null>(null);
    const [copiedKind, setCopiedKind] = useState<"original" | "translation" | null>(null);

    // Image measurement state for precise overlay anchoring
    const [imageRect, setImageRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);
    const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);

    const containerRef = useRef<HTMLDivElement>(null);
    const imgRef = useRef<HTMLImageElement>(null);
    const isDraggingRef = useRef(false);
    const dragRectRef = useRef<DOMRect | null>(null);
    const sliderPosRef = useRef(50);
    const pendingSliderPosRef = useRef(50);
    const sliderFrameRef = useRef<number | null>(null);
    const sliderBadgeRef = useRef<HTMLDivElement>(null);
    const imageMeasureFrameRef = useRef<number | null>(null);

    const showBubbleBoxes = controlledShowBubbleBoxes ?? internalShowBubbleBoxes;
    const setShowBubbleBoxes = (show: boolean) => {
      setInternalShowBubbleBoxes(show);
      onToggleBubbleBoxes?.(show);
      if (!show) {
        setSelectedBlockId(null);
      }
    };

    const viewMode = controlledViewMode ?? internalViewMode;
    const setViewMode = (mode: "split" | "translated" | "inpainted" | "original") => {
      setInternalViewMode(mode);
      onViewModeChange?.(mode);
    };

    const fileName = typeof file === "string"
      ? file
      : file && "name" in file && typeof file.name === "string"
      ? file.name
      : "image";

    // Reset image fallback state when either resolution tier changes.
    useEffect(() => {
      setResultLoadFailed(false);
      setResultLoaded(false);
      setPreviewFallbacks({ original: false, result: false, inpainted: false });
    }, [result, fullResult, originalUrl, inpaintedUrl, fullOriginal, fullInpainted, retryCount, useFullResolution]);

    // Create and revoke ObjectURLs safely
    useEffect(() => {
      if (typeof file === "string") {
        setOriginalUrl(apiUrl(file));
        return;
      }
      if (file instanceof Blob) {
        const url = URL.createObjectURL(file);
        setOriginalUrl(url);
        return () => URL.revokeObjectURL(url);
      }
      setOriginalUrl(null);
    }, [file]);

    useEffect(() => {
      if (typeof inpainted === "string") {
        setInpaintedUrl(apiUrl(inpainted));
        return;
      }
      if (inpainted instanceof Blob) {
        const url = URL.createObjectURL(inpainted);
        setInpaintedUrl(url);
        return () => URL.revokeObjectURL(url);
      }
      setInpaintedUrl(null);
    }, [inpainted]);

    useEffect(() => {
      if (typeof result === "string") {
        const source = apiUrl(result);
        const separator = source.includes("?") ? "&" : "?";
        setResultUrl(retryCount && !source.startsWith("blob:") && !source.startsWith("data:")
          ? `${source}${separator}previewRetry=${retryCount}` : source);
        return;
      }
      if (result instanceof Blob) {
        const url = URL.createObjectURL(result);
        setResultUrl(url);
        return () => URL.revokeObjectURL(url);
      }
      setResultUrl(null);
    }, [result, retryCount]);

    // Warm the browser cache and bitmap decoder before a mode switch.
    useEffect(() => {
      if (!preloadImages) return;
      const urls = [originalUrl, resultUrl, inpaintedUrl].filter(
        (url): url is string => Boolean(url),
      );
      if (urls.length < 2) return;

      const preloads = urls.map((url) => {
        const image = new window.Image();
        image.decoding = "async";
        image.src = url;
        void image.decode().catch(() => {});
        return image;
      });

      return () => {
        preloads.forEach((image) => {
          image.src = "";
        });
      };
    }, [originalUrl, resultUrl, inpaintedUrl, preloadImages]);

    const effectiveOriginalUrl = useFullResolution || previewFallbacks.original ? fullOriginal ? apiUrl(fullOriginal) : originalUrl : originalUrl;
    const fullResultUrl = fullResult ? apiUrl(fullResult) : null;
    const retryFullResultUrl = fullResultUrl && retryCount && !fullResultUrl.startsWith("blob:") && !fullResultUrl.startsWith("data:")
      ? `${fullResultUrl}${fullResultUrl.includes("?") ? "&" : "?"}previewRetry=${retryCount}`
      : fullResultUrl;
    const effectiveResultUrl = useFullResolution || previewFallbacks.result ? retryFullResultUrl ?? resultUrl : resultUrl;
    const effectiveInpaintedUrl = useFullResolution || previewFallbacks.inpainted ? fullInpainted ? apiUrl(fullInpainted) : inpaintedUrl : inpaintedUrl;
    const hasMultiple = [effectiveOriginalUrl, effectiveResultUrl, effectiveInpaintedUrl].filter(Boolean).length >= 2;
    const hasBoth = Boolean(effectiveOriginalUrl && effectiveResultUrl);
    const displayUrl =
      (viewMode === "inpainted" && effectiveInpaintedUrl)
        ? effectiveInpaintedUrl
        : (viewMode === "original" && effectiveOriginalUrl)
        ? effectiveOriginalUrl
        : (effectiveResultUrl || effectiveOriginalUrl || effectiveInpaintedUrl);

    const isShowingTranslatedResult = Boolean(effectiveResultUrl && displayUrl === effectiveResultUrl);
    const resultFeedback = isShowingTranslatedResult && !resultLoaded ? (
      <div role="status" className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-zinc-100/90 dark:bg-zinc-950/90 text-zinc-600 dark:text-zinc-300">
        <span>{resultLoadFailed ? "Image could not be loaded" : "Loading image…"}</span>
        {showComparisonControls && (
          <button type="button" className="rounded bg-indigo-600 px-3 py-2 text-sm text-white"
            onClick={(event) => { event.stopPropagation(); setRetryCount((count) => count + 1); }}>
            Retry image
          </button>
        )}
      </div>
    ) : null;

    const applySliderPosition = (percentage: number) => {
      containerRef.current?.style.setProperty("--slider-pos", `${percentage}%`);
      if (sliderBadgeRef.current) {
        sliderBadgeRef.current.textContent = `${originalUrl ? originalLabel : inpaintedLabel} (${Math.round(percentage)}%)`;
      }
    };

    const commitSliderPosition = () => {
      if (sliderFrameRef.current !== null) {
        cancelAnimationFrame(sliderFrameRef.current);
        sliderFrameRef.current = null;
      }
      const percentage = pendingSliderPosRef.current;
      sliderPosRef.current = percentage;
      applySliderPosition(percentage);
      setSliderPos(percentage);
    };

    // Cache layout once per drag and update the visual directly at frame rate.
    const updateSliderFromPointer = (clientX: number) => {
      const rect = dragRectRef.current;
      if (!rect || rect.width <= 0) return;
      pendingSliderPosRef.current = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
      if (sliderFrameRef.current !== null) return;
      sliderFrameRef.current = requestAnimationFrame(() => {
        sliderFrameRef.current = null;
        const percentage = pendingSliderPosRef.current;
        sliderPosRef.current = percentage;
        applySliderPosition(percentage);
      });
    };

    const handlePointerDown = (e: React.PointerEvent) => {
      isDraggingRef.current = true;
      dragRectRef.current = containerRef.current?.getBoundingClientRect() ?? null;
      containerRef.current?.setPointerCapture(e.pointerId);
      updateSliderFromPointer(e.clientX);
    };

    const handlePointerMove = (e: React.PointerEvent) => {
      if (isDraggingRef.current) updateSliderFromPointer(e.clientX);
    };

    const handlePointerUp = (e: React.PointerEvent) => {
      commitSliderPosition();
      isDraggingRef.current = false;
      dragRectRef.current = null;
      if (containerRef.current?.hasPointerCapture(e.pointerId)) {
        containerRef.current.releasePointerCapture(e.pointerId);
      }
    };

    // Load text regions from prop or URL/folder
    const effectiveBlocks = textRegions ?? internalTextRegions;
    const fallbackOriginalTextLines = useMemo<DetectedRegionLine[]>(() => {
      return effectiveBlocks.flatMap((block) => {
        const lines = block.lines && block.lines.length > 0
          ? block.lines
          : [[[block.x, block.y], [block.x + block.width, block.y], [block.x + block.width, block.y + block.height], [block.x, block.y + block.height]] as Array<[number, number]>];
        const confidence = typeof block.confidence === "number" ? block.confidence : (typeof block.prob === "number" ? block.prob : null);
        return lines.map((pts) => ({
          points: pts,
          confidence,
        }));
      });
    }, [effectiveBlocks]);
    const originalTextLines: DetectedRegionLine[] = detectedTextLines ?? fallbackOriginalTextLines;
    const imageCoordinateSize = coordinateSize ?? naturalSize;
    const bubbleCoordinateSize = detectedBubbleRegions.find((region) => region.imageSize)?.imageSize ?? imageCoordinateSize;
    const originalRegionCount = detectedTextLines?.length ?? countOriginalTextRegions(effectiveBlocks);
    const hasOriginalRegionData = originalRegionCount > 0;
    const effectiveTextRegionsUrl = textRegionsUrl
      ? (textRegionsUrl.startsWith("http") || textRegionsUrl.startsWith("blob:") ? textRegionsUrl : apiUrl(textRegionsUrl))
      : folder
      ? apiUrl(`/api/result/${folder}/text_regions.json`)
      : null;
    const textRegionsUrls = Array.from(new Set([
      effectiveTextRegionsUrl,
      folder ? apiUrl(`/api/result/${folder}/text_regions.json`) : null,
    ].filter((url): url is string => Boolean(url))));

    useEffect(() => {
      if (textRegions && textRegions.length > 0) {
        setInternalTextRegions(textRegions);
        onTextRegionsLoaded?.(textRegions);
        return;
      }
      if (!effectiveTextRegionsUrl) {
        setInternalTextRegions([]);
        onTextRegionsLoaded?.([]);
        return;
      }

      let isMounted = true;
      setIsLoadingRegions(true);

      const loadRegions = async () => {
        let lastError: unknown = null;
        for (const url of textRegionsUrls) {
          try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return parseTextRegions(await res.json());
          } catch (error) {
            lastError = error;
          }
        }
        throw lastError ?? new Error("No text-region URL");
      };

      loadRegions()
        .then((parsed) => {
          if (!isMounted) return;
          setInternalTextRegions(parsed);
          onTextRegionsLoaded?.(parsed);
        })
        .catch((err) => {
          if (isMounted) {
            console.warn("PreviewImage could not load text regions:", err);
            setInternalTextRegions([]);
            onTextRegionsLoaded?.([]);
          }
        })
        .finally(() => {
          if (isMounted) {
            setIsLoadingRegions(false);
          }
        });

      return () => {
        isMounted = false;
      };
    }, [textRegions, effectiveTextRegionsUrl, textRegionsUrls.join("|")]);

    useEffect(() => {
      if (!folder) {
        setDetectedTextLines(null);
        return;
      }

      let isMounted = true;
      setDetectedTextLines(null);
      fetch(apiUrl(`/api/result/${folder}/detection.json`))
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((data) => {
          if (!isMounted) return;
          const lines = parseDetectionRegions(data);
          setDetectedTextLines(lines);
          onOriginalRegionCountLoaded?.(lines.length);
        })
        .catch((error) => {
          if (isMounted) console.warn("PreviewImage could not load detector regions:", error);
        });

      return () => {
        isMounted = false;
      };
    }, [folder, onOriginalRegionCountLoaded]);

    useEffect(() => {
      if (!showBubbleRegions) return;
      if (!folder) {
        setDetectedBubbleRegions([]);
        setBubbleRegionsStatus("error");
        return;
      }

      let isMounted = true;
      setDetectedBubbleRegions([]);
      setBubbleRegionsStatus("loading");
      fetch(apiUrl(`/api/result/${encodeURIComponent(folder)}/bubble_detections.json`))
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((data) => {
          if (!isMounted) return;
          setDetectedBubbleRegions(parseBubbleDetections(data));
          setBubbleRegionsStatus("loaded");
        })
        .catch((error) => {
          if (!isMounted) return;
          console.warn("PreviewImage could not load speech bubble regions:", error);
          setDetectedBubbleRegions([]);
          setBubbleRegionsStatus("error");
        });

      return () => {
        isMounted = false;
      };
    }, [folder, showBubbleRegions]);

    // Measure rendered image rect within container
    const updateImageRect = useCallback(() => {
      if (imageMeasureFrameRef.current !== null) return;
      imageMeasureFrameRef.current = requestAnimationFrame(() => {
        imageMeasureFrameRef.current = null;
        if (!imgRef.current || !containerRef.current) return;
        const imgBounds = imgRef.current.getBoundingClientRect();
        const containerBounds = containerRef.current.getBoundingClientRect();
        if (imgBounds.width > 0 && imgBounds.height > 0) {
          const nextRect = {
            left: imgBounds.left - containerBounds.left,
            top: imgBounds.top - containerBounds.top,
            width: imgBounds.width,
            height: imgBounds.height,
          };
          setImageRect((previous) => previous &&
            Math.abs(previous.left - nextRect.left) < 0.5 &&
            Math.abs(previous.top - nextRect.top) < 0.5 &&
            Math.abs(previous.width - nextRect.width) < 0.5 &&
            Math.abs(previous.height - nextRect.height) < 0.5
              ? previous
              : nextRect);
        }
        if (imgRef.current.naturalWidth > 0 && imgRef.current.naturalHeight > 0) {
          const nextSize = { width: imgRef.current.naturalWidth, height: imgRef.current.naturalHeight };
          setNaturalSize((previous) => previous?.width === nextSize.width && previous.height === nextSize.height
            ? previous
            : nextSize);
        }
      });
    }, []);

    useEffect(() => {
      updateImageRect();
      if (!containerRef.current) return;
      const ro = new ResizeObserver(() => {
        updateImageRect();
      });
      ro.observe(containerRef.current);
      if (imgRef.current) {
        ro.observe(imgRef.current);
      }
      window.addEventListener("resize", updateImageRect);
      return () => {
        ro.disconnect();
        window.removeEventListener("resize", updateImageRect);
        if (imageMeasureFrameRef.current !== null) {
          cancelAnimationFrame(imageMeasureFrameRef.current);
          imageMeasureFrameRef.current = null;
        }
      };
    }, [updateImageRect, viewMode, isHoldingOriginal, effectiveOriginalUrl, effectiveResultUrl, effectiveInpaintedUrl]);

    const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
      const target = e.currentTarget;
      if (target.src === effectiveResultUrl || (effectiveResultUrl && target.src.includes(effectiveResultUrl))) {
        setResultLoaded(true);
      }
      if (target !== imgRef.current) return;
      if (target.naturalWidth > 0 && target.naturalHeight > 0) {
        setNaturalSize({
          width: target.naturalWidth,
          height: target.naturalHeight,
        });
      }
      updateImageRect();
    };

    const handleCopy = async (text: string, kind: "original" | "translation") => {
      if (!text) return;
      try {
        if (navigator?.clipboard?.writeText) {
          await navigator.clipboard.writeText(text);
        } else {
          const textarea = document.createElement("textarea");
          textarea.value = text;
          document.body.appendChild(textarea);
          textarea.select();
          document.execCommand("copy");
          document.body.removeChild(textarea);
        }
        setCopiedKind(kind);
        setTimeout(() => {
          setCopiedKind((prev) => (prev === kind ? null : prev));
        }, 2000);
      } catch (e) {
        console.error("Failed to copy text:", e);
      }
    };

    useEffect(() => {
      const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === "Escape" && selectedBlockId) {
          setSelectedBlockId(null);
        }
      };
      window.addEventListener("keydown", handleKeyDown);
      return () => window.removeEventListener("keydown", handleKeyDown);
    }, [selectedBlockId]);

    const hasBubbleData = effectiveBlocks.length > 0 || isLoadingRegions || Boolean(effectiveTextRegionsUrl);
    const handleSourceError = (kind: "original" | "result" | "inpainted") => {
      const fullUrl = kind === "original" ? fullOriginal : kind === "result" ? fullResult : fullInpainted;
      if (!useFullResolution && fullUrl && !previewFallbacks[kind]) {
        setPreviewFallbacks((previous) => ({ ...previous, [kind]: true }));
      } else if (kind === "result") {
        setResultLoaded(false);
        setResultLoadFailed(true);
      }
    };

    // If only one image is available AND no comparison/bubble controls needed, render cleanly
    if (!showComparisonControls || (!hasMultiple && !hasBubbleData)) {
      return (
        <div className={`relative flex items-center justify-center w-full h-full ${className}`}>
          {displayUrl ? (
            <img
              key={`${displayUrl}-${retryCount}`}
              src={displayUrl}
              loading={loading}
              onLoad={() => { if (displayUrl === effectiveResultUrl) setResultLoaded(true); }}
              alt={fileName}
              className="max-w-full max-h-full w-auto h-auto object-contain rounded-lg select-none"
              draggable={false}
              onError={() => displayUrl === effectiveOriginalUrl
                ? handleSourceError("original")
                : displayUrl === effectiveInpaintedUrl
                ? handleSourceError("inpainted")
                : handleSourceError("result")}
            />
          ) : (
            <div className="flex items-center justify-center text-zinc-400">
              <Icon icon="carbon:image" className="w-8 h-8 animate-pulse" />
            </div>
          )}
          {resultFeedback}
        </div>
      );
    }

    // Multiple image states available or bubble data present: show comparison viewer
    const effectiveShowOriginal = isHoldingOriginal || (viewMode === "original" && Boolean(effectiveOriginalUrl));
    const effectiveShowInpainted = !isHoldingOriginal && viewMode === "inpainted" && Boolean(effectiveInpaintedUrl);
    const effectiveShowTranslated = !isHoldingOriginal && !effectiveShowInpainted && (viewMode === "translated" || !effectiveOriginalUrl);
    const isSplitView = !effectiveShowOriginal && !effectiveShowInpainted && !effectiveShowTranslated;
    const activeImage = effectiveShowOriginal
      ? "original"
      : effectiveShowInpainted || (isSplitView && !effectiveResultUrl)
      ? "inpainted"
      : "translated";

    const selectedBlockIndex = selectedBlockId ? effectiveBlocks.findIndex((b) => b.id === selectedBlockId) : -1;
    const selectedBlock = selectedBlockIndex !== -1 ? effectiveBlocks[selectedBlockIndex] : null;
    const isCardNearBottom = selectedBlock && imageCoordinateSize && imageCoordinateSize.height > 0
      ? (selectedBlock.y + selectedBlock.height) / imageCoordinateSize.height > 0.62
      : false;
    const clampedCardXPct = selectedBlock && imageCoordinateSize && imageCoordinateSize.width > 0
      ? Math.max(20, Math.min(80, ((selectedBlock.x + selectedBlock.width / 2) / imageCoordinateSize.width) * 100))
      : 50;

    return (
      <div
        ref={containerRef}
        className={`relative flex items-center justify-center w-full h-full ${floatingToolbarPlacement === "below-image" ? "overflow-visible" : "overflow-hidden"} select-none group/preview ${className}`}
        style={{ "--slider-pos": `${sliderPos}%` } as React.CSSProperties}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onClick={() => setSelectedBlockId(null)}
      >
        {!effectiveShowOriginal && !effectiveShowInpainted && resultFeedback}
        {showBubbleRegions && bubbleRegionsStatus !== "idle" && (
          <div role="status" className="absolute bottom-3 left-1/2 z-30 -translate-x-1/2 rounded-md border border-violet-300/20 bg-zinc-950/90 px-2.5 py-1 text-xs text-zinc-100 shadow-lg">
            {bubbleRegionsStatus === "loading"
              ? "Loading speech bubbles…"
              : bubbleRegionsStatus === "error"
              ? "Saved speech bubble regions unavailable"
              : detectedBubbleRegions.length > 0
              ? `${detectedBubbleRegions.length} speech bubbles`
              : "No saved speech bubble regions"}
          </div>
        )}
        {effectiveBlocks.some(block => block.review_required) && (
          <div role="status" className="absolute bottom-3 left-3 z-30 rounded bg-amber-950 px-3 py-2 text-sm text-amber-100">
            Needs editing · {effectiveBlocks.filter(block => block.review_required).length} preserved bubble(s)
          </div>
        )}

        {/* Toggle / View Mode Controls Bar - Anchored on top of the image */}
        {showComparisonControls && showFloatingToolbar && (
          <div
            className="absolute z-30 flex items-center space-x-0.5 rounded-md bg-black/80 p-0.5 text-white opacity-95 shadow-xl backdrop-blur-md border border-white/10 transition-all duration-150 select-none hover:opacity-100"
            style={
              imageRect
                ? {
                    top: `${floatingToolbarPlacement === "below-image" ? imageRect.top + imageRect.height + 8 : Math.max(8, imageRect.top + 8)}px`,
                    left: `${imageRect.left + imageRect.width / 2}px`,
                    transform: "translateX(-50%)",
                  }
                : {
                    top: "8px",
                    left: "50%",
                    transform: "translateX(-50%)",
                  }
            }
            onClick={(e) => e.stopPropagation()}
          >
            {originalUrl && effectiveResultUrl && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setViewMode("split");
                }}
                className={`rounded px-1.5 py-0.5 text-xs font-medium transition-colors ${
                  viewMode === "split"
                    ? "bg-indigo-600 text-white"
                    : "text-zinc-300 hover:text-white"
                }`}
                title="Split comparison slider"
              >
                Split
              </button>
            )}
            {effectiveResultUrl && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setViewMode("translated");
                }}
                className={`rounded px-1.5 py-0.5 text-xs font-medium transition-colors ${
                  viewMode === "translated"
                    ? "bg-indigo-600 text-white"
                    : "text-zinc-300 hover:text-white"
                }`}
                title={`Show ${resultLabel}`}
              >
                {resultLabel}
              </button>
            )}
            {inpaintedUrl && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setViewMode("inpainted");
                }}
                className={`px-2 py-1 text-xs rounded font-medium transition-colors ${
                  viewMode === "inpainted"
                    ? "bg-emerald-600 text-white"
                    : "text-zinc-300 hover:text-white"
                }`}
                title={`Show ${inpaintedLabel} (clean artwork)`}
              >
                {inpaintedLabel}
              </button>
            )}
            {originalUrl && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setViewMode("original");
                }}
                className={`px-2 py-1 text-xs rounded font-medium transition-colors ${
                  viewMode === "original"
                    ? "bg-indigo-600 text-white"
                    : "text-zinc-300 hover:text-white"
                }`}
                title={`Show ${originalLabel}`}
              >
                {originalLabel}
              </button>
            )}
            {originalUrl && (
              <button
                type="button"
                onMouseDown={() => setIsHoldingOriginal(true)}
                onMouseUp={() => setIsHoldingOriginal(false)}
                onMouseLeave={() => setIsHoldingOriginal(false)}
                onTouchStart={() => setIsHoldingOriginal(true)}
                onTouchEnd={() => setIsHoldingOriginal(false)}
                className="px-2 py-1 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-amber-300 font-medium transition-colors active:bg-amber-500 active:text-black select-none ml-1"
                title={`Hold button to peek at ${originalLabel.toLowerCase()}`}
              >
                Hold Peek
              </button>
            )}
            {/* Toggle Speech Bubble Detection Boxes */}
            {hasBubbleData && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setShowBubbleBoxes(!showBubbleBoxes);
                }}
                className={`ml-0.5 flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium transition-colors ${
                  showBubbleBoxes
                    ? "bg-amber-600 text-white shadow-xs"
                    : "text-zinc-300 hover:text-white"
                }`}
                title={
                  isLoadingRegions
                    ? "Loading speech bubbles…"
                    : showBubbleBoxes
                    ? "Hide speech bubble boxes"
                    : "Show speech bubble boxes"
                }
              >
                {isLoadingRegions ? (
                  <Icon icon="carbon:circle-dash" className="w-3.5 h-3.5 animate-spin text-amber-300" />
                ) : (
                  <Icon icon="carbon:chat" className="w-3.5 h-3.5" />
                )}
                <span>Bubbles</span>
                {effectiveBlocks.length > 0 && (
                  <span
                    className={`text-[10px] px-1 py-0.2 rounded-full ${
                      showBubbleBoxes ? "bg-amber-700/80 text-white" : "bg-zinc-800 text-zinc-300"
                    }`}
                  >
                    {effectiveBlocks.length}
                  </span>
                )}
              </button>
            )}
          </div>
        )}

        {/* Only the selected view is mounted; split mode needs both sources. */}
        <div className="relative h-full w-full">
          {effectiveShowOriginal && effectiveOriginalUrl && (
            <img
              ref={imgRef}
              src={effectiveOriginalUrl}
              loading={loading}
              onLoad={handleImageLoad}
              onError={() => handleSourceError("original")}
              alt={`${fileName} (${originalLabel})`}
              className="absolute inset-0 m-auto max-h-full max-w-full rounded-lg object-contain"
              draggable={false}
            />
          )}
          {(effectiveShowInpainted || (isSplitView && !effectiveResultUrl)) && effectiveInpaintedUrl && (
            <img
              ref={activeImage === "inpainted" ? imgRef : undefined}
              src={effectiveInpaintedUrl}
              loading={loading}
              onLoad={handleImageLoad}
              onError={() => handleSourceError("inpainted")}
              alt={`${fileName} (${inpaintedLabel})`}
              className="absolute inset-0 m-auto max-h-full max-w-full rounded-lg object-contain"
              draggable={false}
            />
          )}
          {(effectiveShowTranslated || (isSplitView && Boolean(effectiveResultUrl))) && effectiveResultUrl && (
            <img
              ref={activeImage === "translated" ? imgRef : undefined}
              key={`${effectiveResultUrl}-${retryCount}`}
              src={effectiveResultUrl}
              loading={loading}
              onLoad={handleImageLoad}
              alt={`${fileName} (${resultLabel})`}
              className="absolute inset-0 m-auto max-h-full max-w-full rounded-lg object-contain"
              draggable={false}
              onError={() => handleSourceError("result")}
            />
          )}

          {isSplitView && effectiveOriginalUrl && (
            <>
              <div
                className="pointer-events-none absolute inset-0 flex items-center justify-center"
                style={{ clipPath: "inset(0 calc(100% - var(--slider-pos)) 0 0)" }}
              >
                <img
                    src={effectiveOriginalUrl}
                    loading={loading}
                    alt=""
                    aria-hidden="true"
                    onError={() => handleSourceError("original")}
                    className="max-h-full max-w-full rounded-lg object-contain"
                    draggable={false}
                  />
              </div>
              <div
                className="absolute bottom-0 top-0 z-10 flex cursor-ew-resize items-center justify-center -translate-x-1/2"
                style={{ left: "var(--slider-pos)" }}
                onPointerDown={handlePointerDown}
                onClick={(e) => e.stopPropagation()}
              >
                <div className="h-full w-0.5 bg-white shadow-[0_0_8px_rgba(0,0,0,0.6)]" />
                <div className="absolute top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full border-2 border-indigo-600 bg-white text-indigo-600 shadow-lg transition-transform hover:scale-110 active:scale-95 dark:bg-zinc-900 dark:text-indigo-400">
                  <Icon icon="carbon:arrows-horizontal" className="h-4 w-4" />
                </div>
              </div>
              <div ref={sliderBadgeRef} className="pointer-events-none absolute bottom-2 left-2 rounded bg-black/60 px-2 py-0.5 text-[11px] text-white backdrop-blur-xs select-none">
                {effectiveOriginalUrl ? originalLabel : inpaintedLabel} ({Math.round(sliderPos)}%)
              </div>
              <div className="pointer-events-none absolute bottom-2 right-2 rounded bg-indigo-600/90 px-2 py-0.5 text-[11px] text-white backdrop-blur-xs select-none">
                {effectiveResultUrl ? resultLabel : inpaintedLabel}
              </div>
            </>
          )}
        </div>

        {/* Text Region Overlays & Interactive Bubble Inspector Layer */}
        {(showBubbleRegions || showBubbleBoxes || (showOriginalRegions && hasOriginalRegionData)) && imageRect && imageCoordinateSize && imageCoordinateSize.width > 0 && imageCoordinateSize.height > 0 && (
          <div
            className="absolute pointer-events-none z-20"
            style={{
              left: `${imageRect.left}px`,
              top: `${imageRect.top}px`,
              width: `${imageRect.width}px`,
              height: `${imageRect.height}px`,
            }}
          >
            {showBubbleRegions && detectedBubbleRegions.length > 0 && (
              <svg
                role="img"
                aria-label={`${detectedBubbleRegions.length} detected speech bubbles`}
                className="absolute inset-0 h-full w-full pointer-events-none"
                viewBox={`0 0 ${bubbleCoordinateSize?.width ?? imageCoordinateSize.width} ${bubbleCoordinateSize?.height ?? imageCoordinateSize.height}`}
                preserveAspectRatio="none"
              >
                {detectedBubbleRegions.flatMap((region) => region.polygons.map((polygon, polygonIdx) => (
                  <polygon
                    key={`${region.id}-${polygonIdx}`}
                    points={polygon.map(([x, y]) => `${x},${y}`).join(" ")}
                    fill="#a78bfa"
                    fillOpacity={0.08}
                    stroke="#c084fc"
                    strokeOpacity={0.95}
                    strokeWidth={2.5}
                    vectorEffect="non-scaling-stroke"
                  >
                    <title>{`Speech bubble #${Number(region.id) + 1}`}</title>
                  </polygon>
                )))}
              </svg>
            )}

            {showOriginalRegions && hasOriginalRegionData && (
              <>
                <svg
                  aria-hidden="true"
                  className="absolute inset-0 h-full w-full pointer-events-none"
                  viewBox={`0 0 ${imageCoordinateSize.width} ${imageCoordinateSize.height}`}
                  preserveAspectRatio="none"
                >
                  {originalTextLines.map((region, lineIdx) => {
                    const line = region.points;
                    if (line.length < 3) return null;
                    const confStyle = getConfidenceStyle(region.confidence);
                    const isHovered = hoveredRegionIndex === lineIdx;

                    return (
                      <polygon
                        key={`source-${lineIdx}`}
                        points={line.map(([x, y]) => `${x},${y}`).join(" ")}
                        fill={isHovered ? confStyle.fill : "transparent"}
                        fillOpacity={isHovered ? 0.25 : 0}
                        stroke={confStyle.stroke}
                        strokeDasharray={isHovered ? "none" : "6 4"}
                        strokeOpacity={isHovered ? 1 : 0.75}
                        strokeWidth={isHovered ? 2.5 : 1.5}
                        vectorEffect="non-scaling-stroke"
                        pointerEvents="all"
                        className="pointer-events-auto cursor-pointer transition-all duration-150"
                        onMouseEnter={() => setHoveredRegionIndex(lineIdx)}
                        onMouseLeave={() => setHoveredRegionIndex(null)}
                      />
                    );
                  })}
                </svg>

                {/* Floating tooltip displayed only on hover for the active region */}
                {hoveredRegionIndex !== null && (() => {
                  const region = originalTextLines[hoveredRegionIndex];
                  if (!region || region.points.length < 3) return null;
                  const xs = region.points.map(([x]) => x);
                  const ys = region.points.map(([, y]) => y);
                  const minX = Math.min(...xs);
                  const minY = Math.min(...ys);
                  const maxX = Math.max(...xs);
                  const midX = (minX + maxX) / 2;
                  const leftPct = (midX / imageCoordinateSize.width) * 100;
                  const topPct = (minY / imageCoordinateSize.height) * 100;
                  const conf = region.confidence;
                  const confStyle = getConfidenceStyle(conf);
                  const hasConf = typeof conf === "number" && !isNaN(conf);

                  return (
                    <div
                      key={`hovered-tooltip-${hoveredRegionIndex}`}
                      className="absolute pointer-events-none select-none z-30 animate-in fade-in zoom-in-95 duration-100"
                      style={{
                        left: `${leftPct}%`,
                        top: `${topPct}%`,
                        transform: "translate(-50%, -100%) translateY(-6px)",
                      }}
                    >
                      <div className="flex items-center gap-1.5 rounded-md bg-zinc-950/95 border border-zinc-700/80 px-2 py-1 text-[11px] font-medium text-zinc-100 shadow-xl backdrop-blur-md whitespace-nowrap">
                        <span className={`w-2 h-2 rounded-full shrink-0 ${confStyle.dot}`} />
                        <span className="text-zinc-400 font-mono text-[10px]">#{hoveredRegionIndex + 1}</span>
                        {hasConf ? (
                          <span className="font-mono font-bold text-white">
                            {(conf * 100).toFixed(conf * 100 % 1 === 0 ? 0 : 1)}%
                          </span>
                        ) : (
                          <span className="text-zinc-300">Detected</span>
                        )}
                        <span className="text-zinc-500 text-[10px] capitalize">({confStyle.tier})</span>
                      </div>
                    </div>
                  );
                })()}
              </>
            )}

            {showBubbleBoxes && effectiveBlocks.map((block, idx) => {
              const isSelected = selectedBlockId === block.id;
              const leftPct = (block.x / imageCoordinateSize.width) * 100;
              const topPct = (block.y / imageCoordinateSize.height) * 100;
              const widthPct = (block.width / imageCoordinateSize.width) * 100;
              const heightPct = (block.height / imageCoordinateSize.height) * 100;

              return (
                <div
                  key={block.id || idx}
                  role="button"
                  tabIndex={0}
                  aria-label={`Speech bubble ${idx + 1}`}
                  className={`absolute pointer-events-auto rounded cursor-pointer transition-all duration-150 ${
                    isSelected
                      ? "border-2 border-amber-400 bg-amber-400/25 shadow-[0_0_12px_rgba(251,191,36,0.6)] ring-2 ring-amber-400/50 z-30"
                      : "border-2 border-indigo-400/70 hover:border-indigo-300 bg-indigo-500/15 hover:bg-indigo-500/30 hover:shadow-md z-20"
                  }`}
                  style={{
                    left: `${leftPct}%`,
                    top: `${topPct}%`,
                    width: `${widthPct}%`,
                    height: `${heightPct}%`,
                  }}
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedBlockId(isSelected ? null : block.id);
                    setCopiedKind(null);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      e.stopPropagation();
                      setSelectedBlockId(isSelected ? null : block.id);
                      setCopiedKind(null);
                    }
                  }}
                  title={`Bubble #${idx + 1}: Click to inspect original & translated text`}
                >
                  {/* Number Badge */}
                  <span
                    className={`absolute -top-2.5 -left-1 text-[10px] font-mono font-bold px-1 rounded shadow-xs select-none ${
                      isSelected
                        ? "bg-amber-400 text-black"
                        : "bg-indigo-600 text-white"
                    }`}
                  >
                    {idx + 1}
                  </span>
                </div>
              );
            })}

            {/* Inspection Card for Selected Bubble */}
            {showBubbleBoxes && selectedBlock && selectedBlockIndex !== -1 && (
              <div
                className="absolute pointer-events-auto z-40 w-72 sm:w-84 max-w-[90vw] rounded-xl border border-zinc-700 bg-zinc-900/95 p-3 text-zinc-100 shadow-2xl backdrop-blur-md animate-in fade-in zoom-in-95 duration-150"
                style={{
                  left: `${clampedCardXPct}%`,
                  transform: "translateX(-50%)",
                  ...(isCardNearBottom
                    ? { bottom: `calc(${100 - (selectedBlock.y / imageCoordinateSize.height) * 100}% + 8px)` }
                    : { top: `calc(${((selectedBlock.y + selectedBlock.height) / imageCoordinateSize.height) * 100}% + 8px)` }),
                }}
                onClick={(e) => e.stopPropagation()}
              >
                {/* Header */}
                <div className="flex items-center justify-between border-b border-zinc-800 pb-2 mb-2">
                  <div className="flex items-center gap-1.5">
                    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-amber-500 text-black font-mono text-[11px] font-bold">
                      {selectedBlockIndex + 1}
                    </span>
                    <span className="font-semibold text-xs text-zinc-200">
                      Speech Bubble #{selectedBlockIndex + 1}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setSelectedBlockId(null)}
                    className="rounded-md p-1 text-zinc-400 hover:bg-zinc-800 hover:text-white transition-colors"
                    title="Close (Esc)"
                  >
                    <Icon icon="carbon:close" className="w-4 h-4" />
                  </button>
                </div>

                {/* Original Text */}
                <div className="mb-2.5">
                  <div className="flex items-center justify-between text-[11px] text-zinc-400 mb-1">
                    <span className="font-medium uppercase tracking-wider text-amber-300/90 flex items-center gap-1">
                      <Icon icon="carbon:character-patterns" className="w-3.5 h-3.5" />
                      Original
                    </span>
                    {selectedBlock.original_text && (
                      <button
                        type="button"
                        onClick={() => handleCopy(selectedBlock.original_text || "", "original")}
                        className="flex items-center gap-1 text-[10px] text-zinc-400 hover:text-white transition-colors"
                        title="Copy original text"
                      >
                        <Icon
                          icon={copiedKind === "original" ? "carbon:checkmark" : "carbon:copy"}
                          className={`w-3 h-3 ${copiedKind === "original" ? "text-emerald-400" : ""}`}
                        />
                        <span>{copiedKind === "original" ? "Copied" : "Copy"}</span>
                      </button>
                    )}
                  </div>
                  <div className="rounded-lg bg-black/50 border border-zinc-800 p-2 text-xs font-mono text-zinc-200 break-words whitespace-pre-wrap max-h-28 overflow-y-auto select-text">
                    {selectedBlock.original_text ? (
                      selectedBlock.original_text
                    ) : (
                      <span className="italic text-zinc-500">No original text detected</span>
                    )}
                  </div>
                </div>

                {/* Translated Text */}
                <div>
                  <div className="flex items-center justify-between text-[11px] text-zinc-400 mb-1">
                    <span className="font-medium uppercase tracking-wider text-indigo-400 flex items-center gap-1">
                      <Icon icon="carbon:translate" className="w-3.5 h-3.5" />
                      Translated
                    </span>
                    {selectedBlock.translation && (
                      <button
                        type="button"
                        onClick={() => handleCopy(selectedBlock.translation || "", "translation")}
                        className="flex items-center gap-1 text-[10px] text-zinc-400 hover:text-white transition-colors"
                        title="Copy translated text"
                      >
                        <Icon
                          icon={copiedKind === "translation" ? "carbon:checkmark" : "carbon:copy"}
                          className={`w-3 h-3 ${copiedKind === "translation" ? "text-emerald-400" : ""}`}
                        />
                        <span>{copiedKind === "translation" ? "Copied" : "Copy"}</span>
                      </button>
                    )}
                  </div>
                  <div className="rounded-lg bg-indigo-950/30 border border-indigo-900/50 p-2 text-xs font-sans text-zinc-100 break-words whitespace-pre-wrap max-h-32 overflow-y-auto select-text">
                    {selectedBlock.translation ? (
                      selectedBlock.translation
                    ) : (
                      <span className="italic text-zinc-500">No translation available</span>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    );
  }
);

export default PreviewImage;
