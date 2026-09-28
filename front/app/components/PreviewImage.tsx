import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { Icon } from "@iconify/react";
import { apiUrl } from "@/utils/api";
import {
  countOriginalTextRegions,
  parseBubbleDetections,
  parseDetectionRegions,
  parsePanelDetections,
  parseTextRegions,
  type DetectedBubbleRegion,
  type DetectedPanelRegion,
  type DetectedRegionLine,
} from "@/utils/textRegions";
import type { EditableTextBlock } from "@/types";
import { PreviewOverlay } from "./PreviewOverlay";

interface PreviewImageProps {
  file: Blob | string | null;
  result: Blob | File | string | null;
  inpainted?: Blob | File | string | null;
  folder?: string | null;
  textRegionsUrl?: string | null;
  textRegions?: EditableTextBlock[] | null;
  showBubbleBoxes?: boolean;
  showBubbleRegions?: boolean;
  showPanels?: boolean;
  showOriginalRegions?: boolean;
  onToggleBubbleBoxes?: (show: boolean) => void;
  onTogglePanels?: (show: boolean) => void;
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
  resultPlaceholder?: string | null;
  coordinateSize?: { width: number; height: number } | null;
  workingCoordinateSize?: { width: number; height: number } | null;
  useFullResolution?: boolean;
}

export function getConfidenceStyle(conf: number | null | undefined) {
  if (typeof conf !== "number" || isNaN(conf)) {
    return { stroke: "#f59e0b", fill: "#f59e0b", dot: "bg-amber-400", tier: "detected" };
  }
  if (conf >= 0.85) return { stroke: "#10b981", fill: "#10b981", dot: "bg-emerald-400", tier: "high" };
  if (conf >= 0.65) return { stroke: "#f59e0b", fill: "#f59e0b", dot: "bg-amber-400", tier: "medium" };
  return { stroke: "#ef4444", fill: "#ef4444", dot: "bg-rose-400", tier: "low" };
}

export function getDetectionPaintOrder(regions: Pick<DetectedRegionLine, "points">[]): number[] {
  const area = (points: Array<[number, number]>) => {
    if (points.length < 3) return 0;
    return Math.abs(points.reduce((sum, [x, y], index) => {
      const [nextX, nextY] = points[(index + 1) % points.length];
      return sum + x * nextY - nextX * y;
    }, 0));
  };

  return regions
    .map((region, index) => ({ index, area: area(region.points) }))
    .sort((a, b) => b.area - a.area || a.index - b.index)
    .map(({ index }) => index);
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
    showPanels: controlledShowPanels,
    showOriginalRegions = false,
    onToggleBubbleBoxes,
    onTogglePanels,
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
    resultPlaceholder,
    coordinateSize,
    workingCoordinateSize,
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

    // Bubble, panel detection & text inspection state
    const [internalTextRegions, setInternalTextRegions] = useState<EditableTextBlock[]>([]);
    const [detectedTextLines, setDetectedTextLines] = useState<DetectedRegionLine[] | null>(null);
    const [detectedBubbleRegions, setDetectedBubbleRegions] = useState<DetectedBubbleRegion[]>([]);
    const [bubbleRegionsStatus, setBubbleRegionsStatus] = useState<"idle" | "loading" | "loaded" | "error">("idle");
    const bubbleRegionsLoadedForRef = useRef<string | null>(null);
    const [detectedPanelRegions, setDetectedPanelRegions] = useState<DetectedPanelRegion[]>([]);
    const [panelRegionsStatus, setPanelRegionsStatus] = useState<"idle" | "loading" | "loaded" | "error">("idle");
    const panelRegionsLoadedForRef = useRef<string | null>(null);
    const [isLoadingRegions, setIsLoadingRegions] = useState(false);
    const [internalShowBubbleBoxes, setInternalShowBubbleBoxes] = useState(false);
    const [internalShowPanels, setInternalShowPanels] = useState(false);
    const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);
    const [hoveredRegionIndex, setHoveredRegionIndex] = useState<number | null>(null);
    const [selectedRegionIndex, setSelectedRegionIndex] = useState<number | null>(null);
    const [selectedBubbleId, setSelectedBubbleId] = useState<string | null>(null);
    const [selectedPanelId, setSelectedPanelId] = useState<string | null>(null);
    const [copiedKind, setCopiedKind] = useState<"original" | "translation" | "id" | null>(null);

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

    const showPanels = controlledShowPanels ?? internalShowPanels;
    const setShowPanels = (show: boolean) => {
      setInternalShowPanels(show);
      onTogglePanels?.(show);
      if (!show) {
        setSelectedPanelId(null);
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
      setResultLoaded(Boolean(imgRef.current?.complete && imgRef.current.naturalWidth > 0));
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

    // Warm browser cache and bitmap decoder before mode switch.
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

    useEffect(() => {
      if (!effectiveResultUrl || !resultPlaceholder || resultLoaded || resultLoadFailed) return;
      const timeout = window.setTimeout(() => setResultLoadFailed(true), 15_000);
      return () => window.clearTimeout(timeout);
    }, [effectiveResultUrl, resultLoaded, resultLoadFailed, resultPlaceholder, retryCount, useFullResolution]);

    const displayUrl =
      (viewMode === "inpainted" && effectiveInpaintedUrl)
        ? effectiveInpaintedUrl
        : (viewMode === "original" && effectiveOriginalUrl)
        ? effectiveOriginalUrl
        : (effectiveResultUrl || effectiveOriginalUrl || effectiveInpaintedUrl);

    const isShowingTranslatedResult = Boolean(effectiveResultUrl && displayUrl === effectiveResultUrl);
    const resultFeedback = isShowingTranslatedResult && !resultLoaded ? (
      <div role="status" className={`absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 text-zinc-600 dark:text-zinc-300 ${resultPlaceholder ? "" : "bg-zinc-100/90 dark:bg-zinc-950/90"}`}>
        {resultPlaceholder && <img src={apiUrl(resultPlaceholder)} alt="" className="absolute inset-0 h-full w-full object-contain" />}
        <span className={resultPlaceholder ? "z-10 rounded bg-black/55 px-3 py-2 text-sm text-white backdrop-blur-sm" : ""}>{resultLoadFailed ? "Image could not be loaded" : resultPlaceholder ? "Loading sharper image…" : "Loading image…"}</span>
        {resultLoadFailed && showComparisonControls && (
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
          id: `detection_${block.id}`,
          points: pts,
          confidence,
        }));
      });
    }, [effectiveBlocks]);

    const originalTextLines: DetectedRegionLine[] = detectedTextLines ?? fallbackOriginalTextLines;
    const sourceCoordinateSize = coordinateSize ?? naturalSize;
    const imageCoordinateSize = sourceCoordinateSize && workingCoordinateSize
      && effectiveBlocks.some(block => block.x + block.width > sourceCoordinateSize.width
        || block.y + block.height > sourceCoordinateSize.height)
      ? workingCoordinateSize : sourceCoordinateSize;
    const detectionCoordinateSize = (detectedTextLines ? workingCoordinateSize : null) ?? imageCoordinateSize ?? { width: 1, height: 1 };
    const bubbleCoordinateSize = detectedBubbleRegions.find((region) => region.imageSize)?.imageSize
      ?? workingCoordinateSize ?? imageCoordinateSize;
    const panelCoordinateSize = detectedPanelRegions.find((region) => region.imageSize)?.imageSize
      ?? workingCoordinateSize ?? imageCoordinateSize;
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

    // Load Speech Bubbles
    useEffect(() => {
      if (!showBubbleRegions || (bubbleRegionsStatus === "loaded" && bubbleRegionsLoadedForRef.current === folder)) return;
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
          bubbleRegionsLoadedForRef.current = folder;
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

    // Load Panels
    useEffect(() => {
      if (!showPanels && panelRegionsStatus !== "idle" && panelRegionsLoadedForRef.current === folder) return;
      if (!folder) {
        setDetectedPanelRegions([]);
        setPanelRegionsStatus("idle");
        return;
      }

      let isMounted = true;
      setPanelRegionsStatus("loading");
      fetch(apiUrl(`/api/result/${encodeURIComponent(folder)}/panel_detections.json`))
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((data) => {
          if (!isMounted) return;
          setDetectedPanelRegions(parsePanelDetections(data));
          panelRegionsLoadedForRef.current = folder;
          setPanelRegionsStatus("loaded");
        })
        .catch((error) => {
          if (!isMounted) return;
          setDetectedPanelRegions([]);
          setPanelRegionsStatus("error");
        });

      return () => {
        isMounted = false;
      };
    }, [folder, showPanels]);

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
          if (imgRef.current.complete) setResultLoaded(true);
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

    const handleCopy = async (text: string, kind: "original" | "translation" | "id") => {
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
        if (e.key === "Escape" && (selectedBlockId || selectedRegionIndex !== null || selectedBubbleId || selectedPanelId)) {
          setSelectedBlockId(null);
          setSelectedRegionIndex(null);
          setSelectedBubbleId(null);
          setSelectedPanelId(null);
        }
      };
      window.addEventListener("keydown", handleKeyDown);
      return () => window.removeEventListener("keydown", handleKeyDown);
    }, [selectedBlockId, selectedRegionIndex, selectedBubbleId, selectedPanelId]);

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

    const effectiveShowOriginal = isHoldingOriginal || (viewMode === "original" && Boolean(effectiveOriginalUrl));
    const effectiveShowInpainted = !isHoldingOriginal && viewMode === "inpainted" && Boolean(effectiveInpaintedUrl);
    const effectiveShowTranslated = !isHoldingOriginal && !effectiveShowInpainted && (viewMode === "translated" || !effectiveOriginalUrl);
    const isSplitView = !effectiveShowOriginal && !effectiveShowInpainted && !effectiveShowTranslated;
    const activeImage = effectiveShowOriginal
      ? "original"
      : effectiveShowInpainted || (isSplitView && !effectiveResultUrl)
      ? "inpainted"
      : "translated";

    return (
      <div
        ref={containerRef}
        className={`relative flex items-center justify-center w-full h-full ${floatingToolbarPlacement === "below-image" ? "overflow-visible" : "overflow-hidden"} select-none group/preview ${className}`}
        style={{ "--slider-pos": `${sliderPos}%` } as React.CSSProperties}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onClick={() => { setSelectedBlockId(null); setSelectedRegionIndex(null); setSelectedBubbleId(null); setSelectedPanelId(null); }}
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
        {showPanels && panelRegionsStatus !== "idle" && (
          <div role="status" className="absolute bottom-10 left-1/2 z-30 -translate-x-1/2 rounded-md border border-cyan-300/20 bg-zinc-950/90 px-2.5 py-1 text-xs text-cyan-200 shadow-lg">
            {panelRegionsStatus === "loading"
              ? "Loading panels…"
              : panelRegionsStatus === "error"
              ? "Saved panel regions unavailable"
              : detectedPanelRegions.length > 0
              ? `${detectedPanelRegions.length} panels detected`
              : "No saved panel regions"}
          </div>
        )}
        {effectiveBlocks.some(block => block.review_required) && (
          <div role="status" className="absolute bottom-3 left-3 z-30 rounded bg-amber-950 px-3 py-2 text-sm text-amber-100">
            Needs editing · {effectiveBlocks.filter(block => block.review_required).length} preserved bubble(s)
          </div>
        )}

        {/* Toggle / View Mode Controls Bar */}
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
                  viewMode === "split" ? "bg-indigo-600 text-white" : "text-zinc-300 hover:text-white"
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
                  viewMode === "translated" ? "bg-indigo-600 text-white" : "text-zinc-300 hover:text-white"
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
                  viewMode === "inpainted" ? "bg-emerald-600 text-white" : "text-zinc-300 hover:text-white"
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
                  viewMode === "original" ? "bg-indigo-600 text-white" : "text-zinc-300 hover:text-white"
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
            {/* Toggle Panels Overlay */}
            {(detectedPanelRegions.length > 0 || panelRegionsStatus === "loading" || folder) && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setShowPanels(!showPanels);
                }}
                className={`ml-0.5 flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium transition-colors ${
                  showPanels ? "bg-cyan-700 text-white shadow-xs" : "text-zinc-300 hover:text-white"
                }`}
                title={
                  panelRegionsStatus === "loading"
                    ? "Loading panels…"
                    : showPanels
                    ? "Hide panels"
                    : "Show panels"
                }
              >
                {panelRegionsStatus === "loading" ? (
                  <Icon icon="carbon:circle-dash" className="w-3.5 h-3.5 animate-spin text-cyan-300" />
                ) : (
                  <Icon icon="carbon:grid" className="w-3.5 h-3.5" />
                )}
                <span>Panels</span>
                {detectedPanelRegions.length > 0 && (
                  <span
                    className={`text-[10px] px-1 py-0.2 rounded-full ${
                      showPanels ? "bg-cyan-800 text-white" : "bg-zinc-800 text-zinc-300"
                    }`}
                  >
                    {detectedPanelRegions.length}
                  </span>
                )}
              </button>
            )}
            {/* Toggle Speech Bubble Boxes */}
            {hasBubbleData && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setShowBubbleBoxes(!showBubbleBoxes);
                }}
                className={`ml-0.5 flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium transition-colors ${
                  showBubbleBoxes ? "bg-amber-600 text-white shadow-xs" : "text-zinc-300 hover:text-white"
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

        {/* View mode image render */}
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
          {(effectiveShowTranslated || (isSplitView && effectiveResultUrl)) && effectiveResultUrl && (
            <img
              ref={activeImage === "translated" ? imgRef : undefined}
              key={`${effectiveResultUrl}-${retryCount}`}
              src={effectiveResultUrl}
              loading={loading}
              onLoad={handleImageLoad}
              onError={() => handleSourceError("result")}
              alt={`${fileName} (${resultLabel})`}
              className="absolute inset-0 m-auto max-h-full max-w-full rounded-lg object-contain"
              draggable={false}
            />
          )}
          {isSplitView && (
            <>
              <div
                className="absolute inset-0 overflow-hidden"
                style={{ clipPath: "polygon(0 0, var(--slider-pos) 0, var(--slider-pos) 100%, 0 100%)" }}
              >
                {effectiveOriginalUrl ? (
                  <img
                    src={effectiveOriginalUrl}
                    loading={loading}
                    onLoad={handleImageLoad}
                    onError={() => handleSourceError("original")}
                    alt={`${fileName} (${originalLabel})`}
                    className="absolute inset-0 m-auto max-h-full max-w-full rounded-lg object-contain"
                    draggable={false}
                  />
                ) : effectiveInpaintedUrl ? (
                  <img
                    src={effectiveInpaintedUrl}
                    loading={loading}
                    onLoad={handleImageLoad}
                    onError={() => handleSourceError("inpainted")}
                    alt={`${fileName} (${inpaintedLabel})`}
                    className="absolute inset-0 m-auto max-h-full max-w-full rounded-lg object-contain"
                    draggable={false}
                  />
                ) : null}
              </div>
              <div
                className="absolute inset-y-0 w-0.5 bg-white shadow-[0_0_8px_rgba(0,0,0,0.8)] cursor-ew-resize z-20 touch-none"
                style={{ left: "var(--slider-pos)" }}
                onPointerDown={handlePointerDown}
              >
                <div className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-6 h-6 rounded-full bg-white shadow-md flex items-center justify-center text-zinc-700 hover:scale-110 active:scale-95 transition-transform">
                  <Icon icon="carbon:arrows-horizontal" className="w-3.5 h-3.5" />
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

        {/* Text Region Overlays & Interactive Panel / Bubble Inspector Layer */}
        {(showBubbleRegions || showPanels || showBubbleBoxes || (showOriginalRegions && hasOriginalRegionData)) && imageRect && imageCoordinateSize && imageCoordinateSize.width > 0 && imageCoordinateSize.height > 0 && (
          <div
            className="absolute pointer-events-none z-20"
            style={{
              left: `${imageRect.left}px`,
              top: `${imageRect.top}px`,
              width: `${imageRect.width}px`,
              height: `${imageRect.height}px`,
            }}
          >
            <PreviewOverlay
              imageCoordinateSize={imageCoordinateSize}
              bubbleCoordinateSize={bubbleCoordinateSize}
              panelCoordinateSize={panelCoordinateSize}
              detectionCoordinateSize={detectionCoordinateSize}
              showBubbleRegions={showBubbleRegions}
              detectedBubbleRegions={detectedBubbleRegions}
              selectedBubbleId={selectedBubbleId}
              onSelectBubbleId={setSelectedBubbleId}
              showPanels={showPanels}
              detectedPanelRegions={detectedPanelRegions}
              selectedPanelId={selectedPanelId}
              onSelectPanelId={setSelectedPanelId}
              showOriginalRegions={showOriginalRegions}
              hasOriginalRegionData={hasOriginalRegionData}
              originalTextLines={originalTextLines}
              hoveredRegionIndex={hoveredRegionIndex}
              onHoverRegionIndex={setHoveredRegionIndex}
              selectedRegionIndex={selectedRegionIndex}
              onSelectRegionIndex={setSelectedRegionIndex}
              showBubbleBoxes={showBubbleBoxes}
              effectiveBlocks={effectiveBlocks}
              selectedBlockId={selectedBlockId}
              onSelectBlockId={setSelectedBlockId}
              copiedKind={copiedKind}
              onCopy={handleCopy}
            />
          </div>
        )}
      </div>
    );
  }
);

export default PreviewImage;
