import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { Icon } from "@iconify/react";
import { PreviewImage } from "@/components/PreviewImage";
import { apiUrl } from "@/utils/api";
import { languageOptions } from "@/config";
import { resultFolderFromUrl } from "@/utils/resultPaths";
import { countOriginalTextRegions } from "@/utils/textRegions";
import type { FinishedImage, TranslationSettings } from "@/types";
import type { PipelineLabManifest } from "@/utils/pipelineLab";

export const formatElapsedTime = (durationMs?: number | null): string => {
  if (durationMs == null || !Number.isFinite(durationMs) || durationMs < 0) return "—";
  if (durationMs < 1000) return `${Math.round(durationMs)} ms`;
  return `${(durationMs / 1000).toFixed(durationMs < 10000 ? 2 : 1)} s`;
};

const DEFAULT_TRANSLATOR_MODELS: Record<string, string> = {
  deepseek: "deepseek-chat",
  gemini: "gemini-1.5-flash-002",
  openai: "gpt-5.4-mini",
  groq: "mixtral-8x7b-32768",
  openrouter: "deepseek/deepseek-v4-flash-0731",
  sugoi: "Sugoi V4.0",
  sakura: "Sakura",
  custom_openai: "Custom OpenAI",
  deepl: "DeepL",
  youdao: "Youdao",
  baidu: "Baidu",
  caiyun: "Caiyun",
};

const TRANSLATOR_ENGINES: Record<string, string> = {
  deepseek: "DeepSeek",
  gemini: "Gemini",
  openai: "OpenAI",
  groq: "Groq",
  openrouter: "OpenRouter",
  sugoi: "Sugoi",
  sakura: "Sakura",
  custom_openai: "Custom OpenAI",
  deepl: "DeepL",
  youdao: "Youdao",
  baidu: "Baidu",
  caiyun: "Caiyun",
};

export const DETECTOR_LABELS: Record<string, string> = {
  default: "Default (DBNet)",
  dbnet: "Default (DBNet)",
  dbconvnext: "DBNet ConvNeXt",
  ctd: "Comic Text Detector",
  craft: "CRAFT",
  paddle: "PaddleOCR",
  none: "Disabled",
};

export const OCR_LABELS: Record<string, string> = {
  "48px": "48px (Recommended)",
  "32px": "32px",
  "48px_ctc": "48px CTC",
  mocr: "Manga OCR",
};

export const INPAINTER_LABELS: Record<string, string> = {
  lama_large: "LaMa Large",
  lama_mpe: "LaMa MPE",
  sd: "Stable Diffusion",
  default: "Default (AOT)",
  original: "Original",
  none: "None",
};

export const COLORIZER_LABELS: Record<string, string> = {
  mc2: "MC2 (Manga Colorization V2)",
  none: "Disabled",
};

export const UPSCALER_LABELS: Record<string, string> = {
  "4xultrasharp": "4x UltraSharp",
  esrgan: "RealESRGAN",
  waifu2x: "Waifu2x",
};

export const RENDERER_LABELS: Record<string, string> = {
  default: "Default",
  manga2eng: "Manga2Eng",
  manga2eng_pillow: "Manga2Eng Pillow",
  none: "Disabled",
};

const stringValue = (...values: unknown[]): string | null => {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return null;
};

const numberValue = (...values: unknown[]): number | undefined => {
  for (const val of values) {
    if (typeof val === "number" && Number.isFinite(val)) return val;
    if (typeof val === "string" && val.trim() !== "" && !Number.isNaN(Number(val))) return Number(val);
  }
  return undefined;
};

const booleanValue = (...values: unknown[]): boolean | undefined => {
  for (const val of values) {
    if (typeof val === "boolean") return val;
    if (typeof val === "string") {
      const lower = val.trim().toLowerCase();
      if (lower === "true" || lower === "1") return true;
      if (lower === "false" || lower === "0") return false;
    }
  }
  return undefined;
};

const recordValue = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" ? value as Record<string, unknown> : {};

interface ProfessionalAuditRegion {
  id?: string;
  source?: string;
  draft?: string;
  final?: string;
  confidence?: number;
  review_reasons?: string[];
}

interface ProfessionalStoryAnalysis {
  start_page?: number;
  end_page?: number;
  confidence?: number;
  summary?: string;
  characters?: unknown;
  relationships?: unknown;
  glossary?: unknown;
  voice_notes?: unknown;
  continuity?: unknown;
  ambiguities?: unknown;
}

interface ProfessionalTranslationAudit {
  regions?: ProfessionalAuditRegion[];
  storyIndex?: number | null;
  analysis?: { stories?: ProfessionalStoryAnalysis[] };
}

const formatProfessionalAnalysisValue = (value: unknown): string => {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(formatProfessionalAnalysisValue).filter(Boolean).join(" · ");
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, entry]) => `${key}: ${formatProfessionalAnalysisValue(entry)}`)
      .filter(Boolean)
      .join(" · ");
  }
  return String(value);
};

export const resolveLanguageName = (code?: string | null): string | undefined => {
  if (!code) return undefined;
  const match = languageOptions.find((opt) => opt.value.toLowerCase() === code.toLowerCase());
  return match ? `${match.label} (${code.toUpperCase()})` : code.toUpperCase();
};

export const resolveTranslatorEngine = (translator: unknown): string => {
  const key = stringValue(translator)?.toLowerCase();
  return (key && TRANSLATOR_ENGINES[key]) || stringValue(translator) || "Unknown";
};

export const resolveTranslatorModel = (
  translator: unknown,
  settings: Partial<TranslationSettings> | null | undefined,
  manifest: PipelineLabManifest | null | undefined,
  translationDetail: Record<string, unknown> | null | undefined,
): string => {
  const detailTranslator = recordValue(translationDetail?.translator);
  const configTranslator = recordValue(recordValue(manifest?.config).translator);
  const settingsRecord = recordValue(settings);
  return stringValue(
    detailTranslator.model,
    detailTranslator.modelName,
    translationDetail?.model,
    settingsRecord.translatorModel,
    settingsRecord.geminiModel,
    settingsRecord.offlineModel,
    configTranslator.model,
    configTranslator.model_name,
    configTranslator.modelName,
    recordValue(manifest?.config).model,
    translator && DEFAULT_TRANSLATOR_MODELS[String(translator).toLowerCase()],
  ) || "—";
};

export interface ResolvedPipelineStepSettings {
  detection: {
    detector: string;
    detectorLabel: string;
    resolution?: string;
    boxThreshold?: number;
    unclipRatio?: number;
    textThreshold?: number;
  };
  ocr: {
    model: string;
    modelLabel: string;
    prob?: number;
    useMocrMerge?: boolean;
    minTextLength?: number;
    ignoreBubble?: number;
  };
  bubbleDetection: {
    enabled: boolean;
    model: string;
    confidence?: number;
    maskThreshold?: number;
    size?: number;
  };
  inpainting: {
    inpainter: string;
    inpainterLabel: string;
    size?: string;
    precision?: string;
    maskDilation?: number;
  };
  rendering: {
    renderer: string;
    rendererLabel: string;
    direction: string;
    alignment: string;
    font?: string;
    letterCase?: string;
    letterCaseLabel?: string;
  };
  translation: {
    engine: string;
    model: string;
    sourceLanguage?: string;
    targetLanguage?: string;
  };
  colorization?: {
    enabled: boolean;
    colorizer: string;
    colorizerLabel: string;
    size?: string;
    denoiseSigma?: number;
    colorThreshold?: number;
  };
  upscaling?: {
    enabled: boolean;
    upscaler: string;
    upscalerLabel: string;
    ratio?: string | number;
    revertUpscaling?: boolean;
  };
}

export const resolvePipelineStepSettings = (
  settings: Partial<TranslationSettings> | null | undefined,
  manifest: PipelineLabManifest | null | undefined,
  translationDetail: Record<string, unknown> | null | undefined,
): ResolvedPipelineStepSettings => {
  const manifestConfig = recordValue(manifest?.config);
  const cfgDetector = recordValue(manifestConfig.detector);
  const cfgOcr = recordValue(manifestConfig.ocr);
  const cfgBubble = recordValue(manifestConfig.bubble_detection);
  const cfgInpainter = recordValue(manifestConfig.inpainter);
  const cfgRender = recordValue(manifestConfig.render);
  const cfgColorizer = recordValue(manifestConfig.colorizer);
  const cfgUpscale = recordValue(manifestConfig.upscale);
  const cfgTranslator = recordValue(manifestConfig.translator);
  const detailTranslator = recordValue(translationDetail?.translator);

  const detectorKey = (
    stringValue(cfgDetector.detector, settings?.textDetector) || "default"
  ).toLowerCase();
  const detectorLabel = DETECTOR_LABELS[detectorKey] || detectorKey;
  const detectionResolution = stringValue(
    cfgDetector.detection_size,
    settings?.detectionResolution,
  );
  const boxThreshold = numberValue(
    cfgDetector.box_threshold,
    settings?.customBoxThreshold,
  );
  const unclipRatio = numberValue(
    cfgDetector.unclip_ratio,
    settings?.customUnclipRatio,
  );
  const textThreshold = numberValue(cfgDetector.text_threshold);

  const ocrKey = (stringValue(cfgOcr.ocr, settings?.ocr) || "48px").toLowerCase();
  const ocrLabel = OCR_LABELS[ocrKey] || ocrKey;
  const ocrProb = numberValue(
    cfgOcr.prob,
    settings?.customOcrProb,
    settings?.ocrMinConfidence,
  );
  const useMocrMerge = booleanValue(cfgOcr.use_mocr_merge, settings?.useMocrMerge);
  const minTextLength = numberValue(cfgOcr.min_text_length);
  const ignoreBubble = numberValue(cfgOcr.ignore_bubble);

  const bubbleEnabled = booleanValue(cfgBubble.enabled, settings?.bubbleDetection) ?? false;
  const bubbleModel = stringValue(cfgBubble.model, settings?.bubbleModel) || "manga109";
  const bubbleConfidence = numberValue(cfgBubble.confidence, settings?.bubbleConfidence);
  const bubbleMaskThreshold = numberValue(cfgBubble.mask_threshold, settings?.bubbleMaskThreshold);
  const bubbleSize = numberValue(cfgBubble.image_size);

  const inpainterKey = (
    stringValue(cfgInpainter.inpainter, settings?.inpainter) || "lama_large"
  ).toLowerCase();
  const inpainterLabel = INPAINTER_LABELS[inpainterKey] || inpainterKey;
  const inpaintingSize = stringValue(cfgInpainter.inpainting_size, settings?.inpaintingSize);
  const inpaintingPrecision = stringValue(cfgInpainter.inpainting_precision, settings?.inpaintingPrecision);
  const maskDilation = numberValue(manifestConfig.mask_dilation_offset, settings?.maskDilationOffset);

  const rendererKey = (
    stringValue(cfgRender.renderer, settings?.renderer) || "default"
  ).toLowerCase();
  const rendererLabel = RENDERER_LABELS[rendererKey] || rendererKey;
  const renderDirection = stringValue(cfgRender.direction, settings?.renderTextDirection) || "auto";
  const renderAlignment = stringValue(cfgRender.alignment, settings?.renderAlignment) || "auto";
  const renderFont = stringValue(cfgRender.gimp_font, settings?.renderFont);
  const isUppercase = Boolean(cfgRender.uppercase || settings?.uppercase || settings?.letterCase === "uppercase");
  const isLowercase = Boolean(cfgRender.lowercase || settings?.lowercase || settings?.letterCase === "lowercase");
  const letterCaseKey = isUppercase ? "uppercase" : isLowercase ? "lowercase" : "none";
  const letterCaseLabel = isUppercase ? "ALL CAPS" : isLowercase ? "lowercase" : "Original";

  const translatorKey = stringValue(
    detailTranslator.name,
    settings?.translator,
    cfgTranslator.translator,
  );
  const engine = resolveTranslatorEngine(translatorKey);
  const model = resolveTranslatorModel(translatorKey, settings, manifest, translationDetail);
  const rawSource = stringValue(
    detailTranslator.sourceLanguage,
    cfgTranslator.source_lang,
  );
  const rawTarget = stringValue(
    detailTranslator.targetLanguage,
    settings?.targetLanguage,
    cfgTranslator.target_lang,
  );
  const sourceLanguage = rawSource ? (rawSource.toLowerCase() === "auto" ? "Auto" : resolveLanguageName(rawSource)) : undefined;
  const targetLanguage = rawTarget ? resolveLanguageName(rawTarget) : undefined;

  const colorizerKey = (
    stringValue(cfgColorizer.colorizer, settings?.colorizer) || "none"
  ).toLowerCase();
  const colorizerLabel = COLORIZER_LABELS[colorizerKey] || colorizerKey;
  const colorizerSize = stringValue(cfgColorizer.colorization_size, settings?.colorizationSize);
  const denoiseSigma = numberValue(cfgColorizer.denoise_sigma, settings?.denoiseSigma);
  const colorThreshold = numberValue(
    cfgColorizer.color_threshold,
    cfgColorizer.color_tolerance,
    settings?.colorThreshold,
    settings?.colorTolerance,
  );

  const upscalerKey = (
    stringValue(cfgUpscale.upscaler, settings?.upscaler) || ""
  ).toLowerCase();
  const upscalerLabel = UPSCALER_LABELS[upscalerKey] || (upscalerKey ? upscalerKey : "None");
  const upscaleRatio = cfgUpscale.upscale_ratio ?? settings?.upscaleRatio;
  const upscaleEnabled = Boolean(upscaleRatio && Number(upscaleRatio) > 1);
  const revertUpscaling = booleanValue(cfgUpscale.revert_upscaling, settings?.revertUpscaling);

  return {
    detection: {
      detector: detectorKey,
      detectorLabel,
      resolution: detectionResolution ? `${detectionResolution} px` : undefined,
      boxThreshold: boxThreshold != null ? boxThreshold : undefined,
      unclipRatio: unclipRatio != null ? unclipRatio : undefined,
      textThreshold: textThreshold != null ? textThreshold : undefined,
    },
    ocr: {
      model: ocrKey,
      modelLabel: ocrLabel,
      prob: ocrProb != null ? ocrProb : undefined,
      useMocrMerge: useMocrMerge != null ? useMocrMerge : undefined,
      minTextLength: minTextLength && minTextLength > 0 ? minTextLength : undefined,
      ignoreBubble: ignoreBubble && ignoreBubble > 0 ? ignoreBubble : undefined,
    },
    bubbleDetection: {
      enabled: bubbleEnabled,
      model: bubbleModel,
      confidence: bubbleConfidence,
      maskThreshold: bubbleMaskThreshold,
      size: bubbleSize,
    },
    inpainting: {
      inpainter: inpainterKey,
      inpainterLabel,
      size: inpaintingSize ? `${inpaintingSize} px` : undefined,
      precision: inpaintingPrecision ? inpaintingPrecision.toUpperCase() : undefined,
      maskDilation: maskDilation != null ? maskDilation : undefined,
    },
    rendering: {
      renderer: rendererKey,
      rendererLabel,
      direction: renderDirection,
      alignment: renderAlignment,
      font: renderFont || undefined,
      letterCase: letterCaseKey,
      letterCaseLabel,
    },
    translation: {
      engine,
      model,
      sourceLanguage,
      targetLanguage,
    },
    colorization: {
      enabled: colorizerKey !== "none",
      colorizer: colorizerKey,
      colorizerLabel,
      size: colorizerSize ? `${colorizerSize} px` : undefined,
      denoiseSigma,
      colorThreshold,
    },
    upscaling: {
      enabled: upscaleEnabled,
      upscaler: upscalerKey,
      upscalerLabel,
      ratio: upscaleRatio ? `${upscaleRatio}x` : undefined,
      revertUpscaling,
    },
  };
};

export const formatTimestamp = (value?: string | number | Date | null): string => {
  if (value == null) return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (!Number.isFinite(date.getTime())) return "—";
  return [date.getHours(), date.getMinutes(), date.getSeconds()]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
};

export const timestampTooltip = (value?: string | number | Date | null): string | undefined => {
  if (value == null) return undefined;
  const date = value instanceof Date ? value : new Date(value);
  if (!Number.isFinite(date.getTime())) return undefined;
  return `${date.toISOString()} · ${date.toLocaleString()}`;
};

export const resolveTranslationTiming = (
  manifest: PipelineLabManifest | null | undefined,
  finishedAt?: string | number | Date | null,
  startedAt?: string | number | Date | null,
  durationMs?: number | null,
) => {
  const startAt = startedAt || manifest?.createdAt || null;
  const endAt = finishedAt || manifest?.updatedAt || null;
  const startMs = startAt instanceof Date ? startAt.getTime() : startAt ? Date.parse(String(startAt)) : NaN;
  const endMs = endAt instanceof Date ? endAt.getTime() : endAt ? Date.parse(String(endAt)) : NaN;
  const stageDuration = manifest?.stages?.reduce(
    (total, stage) => total + (Number.isFinite(stage.durationMs) ? stage.durationMs || 0 : 0),
    0,
  ) || 0;
  return {
    startAt,
    endAt,
    durationMs: durationMs != null && Number.isFinite(durationMs)
      ? durationMs
      : Number.isFinite(startMs) && Number.isFinite(endMs) && endMs >= startMs
      ? endMs - startMs
      : stageDuration || null,
  };
};

export const shouldLoadTranslationArtifacts = (sourceType?: FinishedImage["sourceType"]): boolean =>
  sourceType !== "original";

export interface ResolvedImageUrls {
  folder: string | null;
  resultUrl: Blob | string | null;
  inpaintedUrl: string | null;
  bubbleMaskUrl: string | null;
  originalUrl: Blob | File | string | null;
}

export const resolveImageUrls = (
  image: FinishedImage | null | undefined,
  apiUrlFn: (path: string) => string = apiUrl,
): ResolvedImageUrls => {
  if (!image) {
    return {
      folder: null,
      resultUrl: null,
      inpaintedUrl: null,
      bubbleMaskUrl: null,
      originalUrl: null,
    };
  }

  const isOriginal = image.sourceType === "original";
  const folder = image.folder
    ?? (typeof image.result === "string" ? resultFolderFromUrl(image.result) : null)
    ?? (image.fullUrl ? resultFolderFromUrl(image.fullUrl) : null);

  const resultUrl =
    image.result instanceof Blob && image.result.size < 1000 && folder
      ? apiUrlFn(`/result/${folder}/final.jpg`)
      : image.result || (folder ? apiUrlFn(`/result/${folder}/final.jpg`) : null);

  const inpaintedUrl = image.inpaintedUrl
    ? apiUrlFn(image.inpaintedUrl)
    : folder
    ? apiUrlFn(`/result/${folder}/inpainted.jpg`)
    : null;
  const bubbleMaskUrl = image.bubbleMaskUrl ? apiUrlFn(image.bubbleMaskUrl) : null;

  let originalUrl: Blob | File | string | null = null;
  if (image.inputUrl) {
    originalUrl = typeof image.inputUrl === "string" ? apiUrlFn(image.inputUrl) : image.inputUrl;
  } else if (isOriginal) {
    originalUrl = image.fullUrl || image.result || (folder ? apiUrlFn(`/result/${folder}/input.png`) : null);
  } else if (folder) {
    originalUrl = apiUrlFn(`/result/${folder}/input.png`);
  }

  return {
    folder,
    resultUrl,
    inpaintedUrl,
    bubbleMaskUrl,
    originalUrl,
  };
};

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
  onRerender,
  titlePrefix = "Page Detail",
}) => {
  const [zoomLevel, setZoomLevel] = useState(1);
  const [viewMode, setViewMode] = useState<"split" | "translated" | "inpainted" | "original" | "bubble-mask">("translated");
  const [showBubbleBoxes, setShowBubbleBoxes] = useState(false);
  const [showOriginalRegions, setShowOriginalRegions] = useState(false);
  const [isHoldingOriginal, setIsHoldingOriginal] = useState(false);
  const [bubbleCount, setBubbleCount] = useState<number | null>(null);
  const [originalRegionCount, setOriginalRegionCount] = useState<number | null>(null);

  const [isRetrying, setIsRetrying] = useState(false);
  const [retryStatus, setRetryStatus] = useState<"queued" | "error" | null>(null);
  const [isRerendering, setIsRerendering] = useState(false);
  const [rerenderStatus, setRerenderStatus] = useState<"queued" | "error" | null>(null);
  const [pipelineManifest, setPipelineManifest] = useState<PipelineLabManifest | null>(null);
  const [isPipelineTimingLoading, setIsPipelineTimingLoading] = useState(false);
  const [translationDetail, setTranslationDetail] = useState<Record<string, unknown> | null>(null);
  const [professionalAudit, setProfessionalAudit] = useState<ProfessionalTranslationAudit | null>(null);
  const [isTranslationDetailLoading, setIsTranslationDetailLoading] = useState(false);
  const [discoveredBubbleMaskUrl, setDiscoveredBubbleMaskUrl] = useState<string | null>(null);

  // Sidebar tab and resize state
  type SidebarTab = "timing" | "localization" | "story" | "settings";
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("timing");
  const [sidebarWidth, setSidebarWidth] = useState(480); // 1.5x of original 320px
  const sidebarResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const isOriginal = image.sourceType === "original";

  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const modalContainerRef = useRef<HTMLDivElement>(null);

  // Focus close button on mount
  useEffect(() => {
    closeButtonRef.current?.focus();
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

  // Derived image URLs
  const resolvedImageUrls = useMemo(() => resolveImageUrls(image), [image]);
  const {
    folder: resolvedFolder,
    resultUrl: resolvedResultUrl,
    inpaintedUrl: resolvedInpaintedUrl,
    bubbleMaskUrl,
    originalUrl: resolvedOriginalUrl,
  } = resolvedImageUrls;
  const resolvedBubbleMaskUrl = bubbleMaskUrl ?? discoveredBubbleMaskUrl;

  useEffect(() => {
    let cancelled = false;
    setDiscoveredBubbleMaskUrl(null);
    if (isOriginal || bubbleMaskUrl || !resolvedFolder) return;

    const maskUrl = apiUrl(`/result/${encodeURIComponent(resolvedFolder)}/bubble_mask.png`);
    fetch(maskUrl, { method: "HEAD", cache: "no-store" })
      .then((response) => {
        if (!cancelled && response.ok) setDiscoveredBubbleMaskUrl(maskUrl);
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [bubbleMaskUrl, isOriginal, resolvedFolder]);

  useEffect(() => {
    let cancelled = false;
    setPipelineManifest(null);
    setIsPipelineTimingLoading(false);
    if (!resolvedFolder || !shouldLoadTranslationArtifacts(image.sourceType)) return;

    setIsPipelineTimingLoading(true);
    fetch(apiUrl(`/pipeline-lab/runs/${encodeURIComponent(resolvedFolder)}/manifest`), {
      cache: "no-store",
    })
      .then(async (response) => {
        if (response.ok) return response.json();
        const fallbackRes = await fetch(apiUrl(`/result/${encodeURIComponent(resolvedFolder)}/pipeline_manifest.json`), {
          cache: "no-store",
        });
        return fallbackRes.ok ? fallbackRes.json() : null;
      })
      .then((manifest: PipelineLabManifest | null) => {
        if (!cancelled && manifest) setPipelineManifest(manifest);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setIsPipelineTimingLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [image.sourceType, resolvedFolder]);

  useEffect(() => {
    let cancelled = false;
    setTranslationDetail(null);
    setProfessionalAudit(null);
    setIsTranslationDetailLoading(false);
    if (!resolvedFolder || !shouldLoadTranslationArtifacts(image.sourceType)) return;

    setIsTranslationDetailLoading(true);
    Promise.all([
      fetch(apiUrl(`/result/${encodeURIComponent(resolvedFolder)}/translation_detail.json`), {
        cache: "no-store",
      }).then((response) => (response.ok ? response.json() : null)),
      fetch(apiUrl(`/result/${encodeURIComponent(resolvedFolder)}/professional_translation.json`), {
        cache: "no-store",
      }).then((response) => (response.ok ? response.json() : null)),
    ])
      .then(([detail, audit]: [Record<string, unknown> | null, ProfessionalTranslationAudit | null]) => {
        if (cancelled) return;
        if (detail) setTranslationDetail(detail);
        if (audit?.regions?.length || audit?.analysis?.stories?.length) setProfessionalAudit(audit);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setIsTranslationDetailLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [image.sourceType, resolvedFolder]);

  // Sidebar resize handlers
  const handleResizeMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    sidebarResizeRef.current = { startX: e.clientX, startWidth: sidebarWidth };
    const onMouseMove = (moveEvent: MouseEvent) => {
      if (!sidebarResizeRef.current) return;
      const delta = sidebarResizeRef.current.startX - moveEvent.clientX;
      const next = Math.max(280, Math.min(700, sidebarResizeRef.current.startWidth + delta));
      setSidebarWidth(next);
    };
    const onMouseUp = () => {
      sidebarResizeRef.current = null;
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  }, [sidebarWidth]);

  // Download handler
  const handleDownload = useCallback(() => {

    if (onDownload) {
      onDownload(image);
      return;
    }
    const targetSource =
      viewMode === "original"
        ? resolvedOriginalUrl
        : viewMode === "inpainted"
        ? resolvedInpaintedUrl
        : viewMode === "bubble-mask"
        ? resolvedBubbleMaskUrl
        : resolvedResultUrl;
    if (!targetSource) return;
    const isBlob = targetSource instanceof Blob;
    const url = isBlob ? URL.createObjectURL(targetSource) : apiUrl(targetSource);
    const anchor = document.createElement("a");
    anchor.href = url;
    const prefix = isOriginal || viewMode === "original" ? "original" : viewMode === "inpainted" ? "inpainted" : viewMode === "bubble-mask" ? "bubble-mask" : "translated";
    const safeName = (image.originalName && image.originalName !== "Unknown") ? image.originalName : `${image.folder || "page"}.png`;
    anchor.download = `${prefix}_${safeName}`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    if (isBlob) setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, [image, onDownload, resolvedResultUrl, resolvedOriginalUrl, resolvedInpaintedUrl, resolvedBubbleMaskUrl, viewMode, isOriginal]);

  const handleRetry = useCallback(async () => {
    if (!onRetry || isRetrying || retryStatus === "queued") return;
    setIsRetrying(true);
    setRetryStatus(null);
    try {
      await onRetry(image);
      setRetryStatus("queued");
    } catch {
      setRetryStatus("error");
    } finally {
      setIsRetrying(false);
    }
  }, [image, isRetrying, onRetry, retryStatus]);

  const handleRerender = useCallback(async () => {
    if (!onRerender || isRerendering || rerenderStatus === "queued") return;
    setIsRerendering(true);
    setRerenderStatus(null);
    try {
      await onRerender(image);
      setRerenderStatus("queued");
    } catch {
      setRerenderStatus("error");
    } finally {
      setIsRerendering(false);
    }
  }, [image, isRerendering, onRerender, rerenderStatus]);

  // Navigation helpers
  const activeIndex = currentIndex ?? (images.length > 0 ? images.findIndex((img) => img.id === image.id) : -1);
  const hasPrev = images.length > 1 && activeIndex > 0;
  const hasNext = images.length > 1 && activeIndex !== -1 && activeIndex < images.length - 1;

  const handlePrev = useCallback(() => {
    if (images.length <= 1) return;
    const prevIdx = activeIndex <= 0 ? images.length - 1 : activeIndex - 1;
    onNavigate?.(prevIdx);
  }, [activeIndex, images.length, onNavigate]);

  const handleNext = useCallback(() => {
    if (images.length <= 1) return;
    const nextIdx = activeIndex >= images.length - 1 ? 0 : activeIndex + 1;
    onNavigate?.(nextIdx);
  }, [activeIndex, images.length, onNavigate]);

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const activeTag = (document.activeElement?.tagName || "").toLowerCase();
      if (activeTag === "input" || activeTag === "textarea" || activeTag === "select") {
        return;
      }

      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }

      if (images.length > 1) {
        if (event.key === "ArrowLeft") {
          event.preventDefault();
          handlePrev();
          return;
        }
        if (event.key === "ArrowRight") {
          event.preventDefault();
          handleNext();
          return;
        }
      }

      if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        setZoomLevel((prev) => Math.min(3, prev + 0.25));
      } else if (event.key === "-" || event.key === "_") {
        event.preventDefault();
        setZoomLevel((prev) => Math.max(0.5, prev - 0.25));
      } else if (event.key === "0") {
        event.preventDefault();
        setZoomLevel(1);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [handleNext, handlePrev, images.length, onClose]);

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

  return (
    <div
      ref={modalContainerRef}
      className="fixed inset-0 z-50 flex flex-col bg-black/90 p-2 backdrop-blur-md sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="page-detail-viewer-title"
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-white/10 bg-zinc-950/95 shadow-2xl">
        {/* Top Header Bar */}
        <header
          className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-white/10 bg-black/40 px-3 py-2.5 text-white sm:px-4"
        >
          <div className="min-w-0 flex shrink items-center gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 text-xs font-medium text-zinc-400">
                {image.mangaTitle && (
                  <span className="rounded bg-white/10 px-1.5 py-0.5 text-[11px] text-zinc-300 font-medium truncate max-w-[140px]">
                    {image.mangaTitle}
                  </span>
                )}
                <span className="uppercase tracking-[0.16em]">
                  {titlePrefix}
                </span>
              </div>
              <h2 id="page-detail-viewer-title" className="truncate text-sm font-semibold sm:text-base">
                {(image.originalName && image.originalName !== "Unknown")
                  ? image.originalName
                  : (image.folder ? `${image.folder}.png` : "Manga Page")}
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
                  onClick={() => setViewMode("bubble-mask")}
                  className={`flex min-h-8 items-center gap-1 rounded px-2 text-xs font-semibold transition-colors cursor-pointer ${
                    viewMode === "bubble-mask"
                      ? "bg-violet-600 text-white shadow-xs"
                      : "text-zinc-300 hover:bg-white/10 hover:text-white"
                  }`}
                  title="Show the detected speech-bubble mask"
                >
                  Bubble mask
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
                className="flex min-h-8 items-center gap-1 rounded-md bg-zinc-800 hover:bg-red-600 px-2.5 text-xs font-semibold text-zinc-300 hover:text-white transition-colors cursor-pointer"
                title="Delete from server"
              >
                <Icon icon="carbon:trash-can" className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Delete</span>
              </button>
            )}

            {/* Close Button */}
            <button
              ref={closeButtonRef}
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
              <div
                className="flex h-[calc(100vh-12rem)] w-full max-w-6xl items-center justify-center transition-transform duration-100 sm:h-[calc(100vh-13rem)]"
                style={{ transform: `scale(${zoomLevel})` }}
              >
                {viewMode === "bubble-mask" && resolvedBubbleMaskUrl ? (
                  <img
                    key={`${image.id}-bubble-mask`}
                    src={resolvedBubbleMaskUrl}
                    alt="Detected speech-bubble mask"
                    className="max-h-full max-w-full rounded-lg shadow-2xl object-contain"
                  />
                ) : (
                  <PreviewImage
                    key={image.id}
                    file={resolvedOriginalUrl}
                    result={isOriginal ? null : resolvedResultUrl}
                    inpainted={isOriginal ? null : resolvedInpaintedUrl}
                    folder={resolvedFolder}
                    textRegionsUrl={image.textRegionsUrl}
                    viewMode={isOriginal ? "original" : viewMode === "bubble-mask" ? "translated" : viewMode}
                    onViewModeChange={isOriginal ? undefined : setViewMode}
                    showBubbleBoxes={showBubbleBoxes}
                    showOriginalRegions={showOriginalRegions}
                    onToggleBubbleBoxes={setShowBubbleBoxes}
                    isHoldingOriginal={isOriginal ? false : isHoldingOriginal}
                    showFloatingToolbar={false}
                    onTextRegionsLoaded={(blocks) => {
                      setBubbleCount(blocks.length);
                      setOriginalRegionCount(countOriginalTextRegions(blocks));
                    }}
                    showComparisonControls={isOriginal ? originalTextAvailable : true}
                    className="max-h-full max-w-full rounded-lg shadow-2xl"
                  />
                )}
              </div>
            </div>
              </div>

              {!isOriginal && (
                <div className="relative hidden lg:flex shrink-0 flex-col" style={{ width: sidebarWidth }}>
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
                          onClick={() => setSidebarTab(tab.id)}
                          className={`flex flex-1 flex-col items-center gap-1 px-1 py-2 text-[10px] font-medium transition-colors cursor-pointer ${
                            sidebarTab === tab.id
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

                      {/* ── Pipeline Timing tab ── */}
                      {sidebarTab === "timing" && (
                        <div className="space-y-4">
                          {/* Start / End / Duration */}
                          <section className="rounded-lg border border-indigo-400/20 bg-indigo-500/10 p-3" aria-label="Translation timing">
                            <div className="mb-2.5 flex items-center justify-between gap-2">
                              <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-indigo-200">
                                <Icon icon="carbon:time" className="h-4 w-4 text-indigo-300" />
                                Timing overview
                              </h3>
                              {isTranslationDetailLoading && (
                                <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin text-indigo-300" aria-label="Loading" />
                              )}
                            </div>
                            <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
                              <div>
                                <dt className="text-zinc-400">Started at</dt>
                                <dd className="mt-0.5 font-mono font-medium text-white" title={timestampTooltip(timing.startAt)}>
                                  <time dateTime={timing.startAt instanceof Date ? timing.startAt.toISOString() : timing.startAt != null ? String(timing.startAt) : undefined}>{formatTimestamp(timing.startAt)}</time>
                                </dd>
                              </div>
                              <div>
                                <dt className="text-zinc-400">Ended at</dt>
                                <dd className="mt-0.5 font-mono font-medium text-white" title={timestampTooltip(timing.endAt)}>
                                  <time dateTime={timing.endAt instanceof Date ? timing.endAt.toISOString() : timing.endAt != null ? String(timing.endAt) : undefined}>{formatTimestamp(timing.endAt)}</time>
                                </dd>
                              </div>
                              <div className="col-span-2 border-t border-white/10 pt-2">
                                <dt className="text-zinc-400">Total duration</dt>
                                <dd className="mt-0.5 font-mono text-base font-semibold text-indigo-100">{formatElapsedTime(timing.durationMs)}</dd>
                              </div>
                            </dl>
                          </section>

                          {/* Per-stage breakdown */}
                          <div>
                            <div className="mb-2 flex items-center justify-between gap-3">
                              <div>
                                <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-zinc-200">
                                  <Icon icon="carbon:flow" className="h-3.5 w-3.5 text-indigo-300" />
                                  Per-stage breakdown
                                </h3>
                                <p className="mt-0.5 text-[11px] text-zinc-400">Elapsed time per process</p>
                              </div>
                              {pipelineManifest && (
                                <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium capitalize text-emerald-300">
                                  {pipelineManifest.status}
                                </span>
                              )}
                            </div>
                            {isPipelineTimingLoading ? (
                              <div className="flex items-center gap-2 py-4 text-xs text-zinc-400">
                                <Icon icon="carbon:renew" className="h-4 w-4 animate-spin text-indigo-300" />
                                Loading timings…
                              </div>
                            ) : pipelineManifest?.stages?.length ? (
                              <div className="space-y-1">
                                {pipelineManifest.stages.map((stage) => (
                                  <div
                                    key={stage.id}
                                    className="flex items-center justify-between gap-3 rounded-lg bg-black/20 px-3 py-2"
                                  >
                                    <div className="min-w-0">
                                      <div className="truncate text-xs font-medium text-zinc-200">
                                        {stage.label || stage.id}
                                      </div>
                                      <div className="mt-0.5 text-xs capitalize text-zinc-400">
                                        {stage.status}
                                      </div>
                                    </div>
                                    <span className="shrink-0 font-mono text-xs text-indigo-200">
                                      {formatElapsedTime(stage.durationMs)}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <div className="py-2 text-xs leading-5 text-zinc-400">
                                No pipeline timing data is available for this page.
                              </div>
                            )}
                          </div>
                        </div>
                      )}

                      {/* ── Professional Localization tab ── */}
                      {sidebarTab === "localization" && (
                        <div>
                          {professionalAudit?.regions?.length ? (
                            <section className="rounded-lg border border-amber-400/25 bg-amber-500/10 p-3" aria-label="Professional localization audit">
                              <div className="mb-2.5 flex items-center justify-between gap-2">
                                <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-amber-200">
                                  <Icon icon="carbon:translate" className="h-4 w-4 text-amber-300" />
                                  Professional localization
                                </h3>
                                <span className="text-[10px] text-amber-200/80">Source · Draft · Final</span>
                              </div>
                              <div className="space-y-2 pr-1">
                                {professionalAudit.regions.map((region, index) => {
                                  const confidence = typeof region.confidence === "number" ? region.confidence : null;
                                  const reasons = region.review_reasons?.filter(Boolean) || [];
                                  return (
                                    <div key={region.id || index} className="rounded-md border border-white/10 bg-black/20 p-2 text-[11px]">
                                      <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-zinc-500">Region {index + 1}</div>
                                      <dl className="space-y-1.5">
                                        <div>
                                          <dt className="text-zinc-500">Japanese source</dt>
                                          <dd className="whitespace-pre-wrap text-zinc-300">{region.source || "—"}</dd>
                                        </div>
                                        <div>
                                          <dt className="text-amber-300/80">First draft</dt>
                                          <dd className="whitespace-pre-wrap text-amber-100">{region.draft || "—"}</dd>
                                        </div>
                                        <div>
                                          <dt className="text-emerald-300/80">Editor pass</dt>
                                          <dd className="whitespace-pre-wrap text-emerald-100">{region.final || "—"}</dd>
                                        </div>
                                      </dl>
                                      {(confidence !== null || reasons.length > 0) && (
                                        <div className="mt-1.5 border-t border-white/10 pt-1.5 text-[10px] text-zinc-400">
                                          {confidence !== null && <span>Confidence {Math.round(confidence * 100)}%</span>}
                                          {reasons.length > 0 && <span>{confidence !== null ? " · " : ""}{reasons.join(" · ")}</span>}
                                        </div>
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                            </section>
                          ) : (
                            <div className="flex flex-col items-center justify-center py-12 text-center text-xs text-zinc-500">
                              <Icon icon="carbon:translate" className="mb-3 h-8 w-8 text-zinc-600" />
                              <p>No professional localization data available for this page.</p>
                            </div>
                          )}
                        </div>
                      )}

                      {/* ── Story Analysis tab ── */}
                      {sidebarTab === "story" && (
                        <div>
                          {professionalAudit?.analysis?.stories?.length ? (
                            <section className="rounded-lg border border-indigo-400/25 bg-indigo-500/10 p-3" aria-label="Professional story analysis">
                              <div className="mb-2.5 flex items-center justify-between gap-2">
                                <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-indigo-200">
                                  <Icon icon="carbon:document" className="h-4 w-4 text-indigo-300" />
                                  Story analysis
                                </h3>
                                <span className="text-[10px] text-indigo-200/80">
                                  {professionalAudit.analysis.stories.length} {professionalAudit.analysis.stories.length === 1 ? "story" : "stories"}
                                </span>
                              </div>
                              <div className="space-y-3 pr-1">
                                {professionalAudit.analysis.stories.map((story, index) => {
                                  const confidence = typeof story.confidence === "number" ? story.confidence : null;
                                  const details = ([
                                    ["Characters", story.characters],
                                    ["Relationships", story.relationships],
                                    ["Voice notes", story.voice_notes],
                                    ["Continuity", story.continuity],
                                    ["Glossary", story.glossary],
                                    ["Ambiguities", story.ambiguities],
                                  ] as Array<[string, unknown]>)
                                    .map(([label, value]): [string, string] => [label, formatProfessionalAnalysisValue(value)])
                                    .filter(([, value]) => value);
                                  return (
                                    <article key={`${story.start_page}-${story.end_page}-${index}`} className="border-b border-white/10 pb-3 last:border-0 last:pb-0">
                                      <div className="mb-1.5 flex items-center justify-between gap-2">
                                        <h4 className="text-xs font-semibold text-white">
                                          Story {index + 1}
                                          {(story.start_page || story.end_page) && (
                                            <span className="ml-1.5 font-normal text-indigo-200/80">
                                              · Pages {story.start_page ?? "?"}–{story.end_page ?? "?"}
                                            </span>
                                          )}
                                        </h4>
                                        {confidence !== null && (
                                          <span className="shrink-0 rounded-full bg-indigo-400/15 px-1.5 py-0.5 text-[10px] font-medium text-indigo-200">
                                            {Math.round(confidence * 100)}% confidence
                                          </span>
                                        )}
                                      </div>
                                      {story.summary && <p className="text-xs leading-5 text-zinc-200">{story.summary}</p>}
                                      {details.length > 0 && (
                                        <dl className="mt-2 space-y-1.5 text-[11px]">
                                          {details.map(([label, value]) => (
                                            <div key={label}>
                                              <dt className="text-indigo-200/70">{label}</dt>
                                              <dd className="whitespace-pre-wrap text-zinc-300">{value}</dd>
                                            </div>
                                          ))}
                                        </dl>
                                      )}
                                    </article>
                                  );
                                })}
                              </div>
                            </section>
                          ) : (
                            <div className="flex flex-col items-center justify-center py-12 text-center text-xs text-zinc-500">
                              <Icon icon="carbon:document" className="mb-3 h-8 w-8 text-zinc-600" />
                              <p>No story analysis data available for this page.</p>
                            </div>
                          )}
                        </div>
                      )}

                      {/* ── Step Settings tab ── */}
                      {sidebarTab === "settings" && (
                        <div className="space-y-3">
                          {/* Translation Details Card */}
                          <section className="rounded-lg border border-indigo-400/20 bg-indigo-500/10 p-3" aria-label="Translation details">
                            <div className="mb-2.5 flex items-center justify-between gap-2">
                              <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-indigo-200">
                                <Icon icon="carbon:machine-learning-model" className="h-4 w-4 text-indigo-300" />
                                Translation details
                              </h3>
                              {isTranslationDetailLoading && (
                                <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin text-indigo-300" aria-label="Loading model details" />
                              )}
                            </div>
                            <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
                              <div>
                                <dt className="text-zinc-400">Engine</dt>
                                <dd className="mt-0.5 truncate font-medium text-white" title={engine}>{engine}</dd>
                              </div>
                              <div>
                                <dt className="text-zinc-400">Model</dt>
                                <dd className="mt-0.5 break-words font-mono font-medium text-indigo-100" title={model}>{model}</dd>
                              </div>
                              {(stepSettings.translation.sourceLanguage || stepSettings.translation.targetLanguage) && (
                                <div className="col-span-2">
                                  <dt className="text-zinc-400">Languages</dt>
                                  <dd className="mt-0.5 font-medium text-zinc-200 truncate" title={`${stepSettings.translation.sourceLanguage || "Auto"} → ${stepSettings.translation.targetLanguage || "ENG"}`}>
                                    {stepSettings.translation.sourceLanguage || "Auto"} → {stepSettings.translation.targetLanguage || "ENG"}
                                  </dd>
                                </div>
                              )}
                            </dl>
                          </section>

                          {/* Detection & OCR Card */}
                          <div className="space-y-2 text-xs">
                            <div className="rounded-lg border border-white/10 bg-black/20 p-2.5">
                              <div className="mb-2 flex items-center gap-1.5 font-semibold text-zinc-200">
                                <Icon icon="carbon:search-locate" className="h-3.5 w-3.5 text-indigo-300" />
                                <span>Detection & OCR</span>
                              </div>
                              <dl className="grid grid-cols-2 gap-x-2 gap-y-1.5 text-[11px]">
                                <div>
                                  <dt className="text-zinc-400">Detector</dt>
                                  <dd className="font-medium text-white truncate" title={stepSettings.detection.detectorLabel}>{stepSettings.detection.detectorLabel}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Resolution</dt>
                                  <dd className="font-mono font-medium text-indigo-200">{stepSettings.detection.resolution || "—"}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Box threshold</dt>
                                  <dd className="font-mono text-zinc-200">{stepSettings.detection.boxThreshold ?? "—"}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Unclip ratio</dt>
                                  <dd className="font-mono text-zinc-200">{stepSettings.detection.unclipRatio ?? "—"}</dd>
                                </div>
                                <div className="col-span-2 border-t border-white/5 pt-1.5 mt-0.5">
                                  <dt className="text-zinc-400">OCR Model</dt>
                                  <dd className="font-medium text-white flex items-center justify-between gap-1">
                                    <span className="truncate" title={stepSettings.ocr.modelLabel}>{stepSettings.ocr.modelLabel}</span>
                                    <div className="flex items-center gap-1 shrink-0">
                                      {stepSettings.ocr.prob != null && (
                                        <span className="rounded bg-amber-500/20 px-1 text-[10px] text-amber-300">Min Conf: {stepSettings.ocr.prob}</span>
                                      )}
                                      {stepSettings.ocr.useMocrMerge && (
                                        <span className="rounded bg-indigo-500/20 px-1 text-[10px] text-indigo-300">Merge BBox</span>
                                      )}
                                    </div>
                                  </dd>
                                </div>
                              </dl>
                            </div>

                            {/* Speech Bubble Detection Card */}
                            <div className="rounded-lg border border-white/10 bg-black/20 p-2.5">
                              <div className="mb-1.5 flex items-center justify-between font-semibold text-zinc-200">
                                <span className="flex items-center gap-1.5">
                                  <Icon icon="carbon:chat" className="h-3.5 w-3.5 text-indigo-300" />
                                  <span>Speech Bubbles</span>
                                </span>
                                <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${stepSettings.bubbleDetection.enabled ? "bg-emerald-500/15 text-emerald-300" : "bg-zinc-500/20 text-zinc-400"}`}>
                                  {stepSettings.bubbleDetection.enabled ? "Enabled" : "Disabled"}
                                </span>
                              </div>
                              {stepSettings.bubbleDetection.enabled ? (
                                <dl className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
                                  <div>
                                    <dt className="text-zinc-400">Model</dt>
                                    <dd className="font-medium text-zinc-200">{stepSettings.bubbleDetection.model}</dd>
                                  </div>
                                  <div>
                                    <dt className="text-zinc-400">Confidence</dt>
                                    <dd className="font-mono text-zinc-200">{stepSettings.bubbleDetection.confidence ?? "—"}</dd>
                                  </div>
                                  <div>
                                    <dt className="text-zinc-400">Mask threshold</dt>
                                    <dd className="font-mono text-zinc-200">{stepSettings.bubbleDetection.maskThreshold ?? "—"}</dd>
                                  </div>
                                  {stepSettings.bubbleDetection.size ? (
                                    <div>
                                      <dt className="text-zinc-400">Input size</dt>
                                      <dd className="font-mono text-zinc-200">{stepSettings.bubbleDetection.size} px</dd>
                                    </div>
                                  ) : null}
                                </dl>
                              ) : (
                                <p className="text-[11px] text-zinc-400">Bubble shape segmentation turned off</p>
                              )}
                            </div>

                            {/* Inpainting & Mask Card */}
                            <div className="rounded-lg border border-white/10 bg-black/20 p-2.5">
                              <div className="mb-2 flex items-center gap-1.5 font-semibold text-zinc-200">
                                <Icon icon="carbon:erase" className="h-3.5 w-3.5 text-indigo-300" />
                                <span>Inpainting & Mask</span>
                              </div>
                              <dl className="grid grid-cols-2 gap-x-2 gap-y-1.5 text-[11px]">
                                <div>
                                  <dt className="text-zinc-400">Inpainter</dt>
                                  <dd className="font-medium text-white truncate" title={stepSettings.inpainting.inpainterLabel}>{stepSettings.inpainting.inpainterLabel}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Resolution</dt>
                                  <dd className="font-mono font-medium text-indigo-200">{stepSettings.inpainting.size || "—"}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Mask dilation</dt>
                                  <dd className="font-mono text-zinc-200">{stepSettings.inpainting.maskDilation != null ? `${stepSettings.inpainting.maskDilation} px` : "—"}</dd>
                                </div>
                                {stepSettings.inpainting.precision && (
                                  <div>
                                    <dt className="text-zinc-400">Precision</dt>
                                    <dd className="font-mono text-zinc-200">{stepSettings.inpainting.precision}</dd>
                                  </div>
                                )}
                              </dl>
                            </div>

                            {/* Rendering Card */}
                            <div className="rounded-lg border border-white/10 bg-black/20 p-2.5">
                              <div className="mb-2 flex items-center gap-1.5 font-semibold text-zinc-200">
                                <Icon icon="carbon:text-font" className="h-3.5 w-3.5 text-indigo-300" />
                                <span>Rendering & Typesetting</span>
                              </div>
                              <dl className="grid grid-cols-2 gap-x-2 gap-y-1.5 text-[11px]">
                                <div>
                                  <dt className="text-zinc-400">Renderer</dt>
                                  <dd className="font-medium text-white capitalize">{stepSettings.rendering.rendererLabel}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Direction</dt>
                                  <dd className="font-medium text-zinc-200 capitalize">{stepSettings.rendering.direction}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Alignment</dt>
                                  <dd className="font-medium text-zinc-200 capitalize">{stepSettings.rendering.alignment}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Lettering Case</dt>
                                  <dd className="font-medium text-zinc-200">{stepSettings.rendering.letterCaseLabel}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Font</dt>
                                  <dd className="font-medium text-zinc-200 truncate" title={stepSettings.rendering.font || "Sans-serif"}>{stepSettings.rendering.font || "Sans-serif"}</dd>
                                </div>
                              </dl>
                            </div>

                            {/* Colorization & Upscaling Card */}
                            {(stepSettings.colorization?.enabled || stepSettings.upscaling?.enabled) ? (
                              <div className="rounded-lg border border-white/10 bg-black/20 p-2.5">
                                <div className="mb-2 flex items-center gap-1.5 font-semibold text-zinc-200">
                                  <Icon icon="carbon:color-palette" className="h-3.5 w-3.5 text-indigo-300" />
                                  <span>Colorization & Upscaling</span>
                                </div>
                                <dl className="grid grid-cols-2 gap-x-2 gap-y-1.5 text-[11px]">
                                  {stepSettings.colorization?.enabled && (
                                    <>
                                      <div>
                                        <dt className="text-zinc-400">Colorizer</dt>
                                        <dd className="font-medium text-white truncate">{stepSettings.colorization.colorizerLabel}</dd>
                                      </div>
                                      <div>
                                        <dt className="text-zinc-400">Color size</dt>
                                        <dd className="font-mono text-zinc-200">{stepSettings.colorization.size || "—"}</dd>
                                      </div>
                                    </>
                                  )}
                                  {stepSettings.upscaling?.enabled && (
                                    <>
                                      <div>
                                        <dt className="text-zinc-400">Upscaler</dt>
                                        <dd className="font-medium text-white truncate">{stepSettings.upscaling.upscalerLabel}</dd>
                                      </div>
                                      <div>
                                        <dt className="text-zinc-400">Upscale ratio</dt>
                                        <dd className="font-mono text-zinc-200">{stepSettings.upscaling.ratio || "—"}</dd>
                                      </div>
                                    </>
                                  )}
                                </dl>
                              </div>
                            ) : null}
                          </div>
                        </div>
                      )}

                    </div>
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
  );
};

export default PageDetailModal;
