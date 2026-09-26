import { languageOptions } from "@/config";
import type { PipelineRunManifest, TranslationSettings } from "@/types";

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
  honorific_policy?: unknown;
  language_features?: unknown;
  localization_conventions?: unknown;
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
  manifest: PipelineRunManifest | null | undefined,
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
  manifest: PipelineRunManifest | null | undefined,
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

