import type { TranslationSettings } from "@/types";

export const COMPARABLE_PRESET_KEYS: (keyof TranslationSettings)[] = [
  "targetLanguage",
  "translator",
  "summaryModel",
  "translationQuality",
  "draftTranslator",
  "renderFont",
  "renderTextDirection",
  "letterCase",
  "detectionResolution",
  "textDetector",
  "ocr",
  "customUnclipRatio",
  "customBoxThreshold",
  "customOcrProb",
  "maskDilationOffset",
  "bubbleDetection",
  "bubbleModel",
  "bubbleConfidence",
  "inpainter",
  "inpaintingSize",
  "colorizer",
  "colorizeOnly",
  "colorizationSize",
  "denoiseSigma",
  "colorThreshold",
  "upscaler",
  "upscaleRatio",
  "revertUpscaling",
  "translationBatchSize",
];

const NUMERIC_KEYS = new Set<keyof TranslationSettings>([
  "customUnclipRatio",
  "customBoxThreshold",
  "customOcrProb",
  "maskDilationOffset",
  "bubbleConfidence",
  "denoiseSigma",
  "colorThreshold",
  "translationBatchSize",
  "upscaleRatio",
]);

const BOOLEAN_KEYS = new Set<keyof TranslationSettings>([
  "bubbleDetection",
  "colorizeOnly",
  "revertUpscaling",
]);

export const normalizePresetValue = (
  key: keyof TranslationSettings,
  val: any,
): string | number | boolean | null => {
  if (val === undefined || val === null || val === "") {
    return null;
  }
  if (BOOLEAN_KEYS.has(key)) {
    return Boolean(val);
  }
  if (NUMERIC_KEYS.has(key)) {
    const num = Number(val);
    return isNaN(num) ? null : num;
  }
  return String(val).trim();
};

export const isSettingsModified = (
  current: TranslationSettings,
  presetSettings: Partial<TranslationSettings>,
): boolean => {
  for (const key of COMPARABLE_PRESET_KEYS) {
    const presetVal = presetSettings[key];
    if (presetVal === undefined) continue;

    const normPreset = normalizePresetValue(key, presetVal);
    const normCurrent = normalizePresetValue(key, current[key]);

    if (normPreset !== normCurrent) {
      return true;
    }
  }
  return false;
};

export const sanitizeSettingsForPreset = (
  settings: TranslationSettings,
): Partial<TranslationSettings> => {
  const result: Partial<TranslationSettings> = {};
  for (const key of COMPARABLE_PRESET_KEYS) {
    const val = settings[key];
    if (val !== undefined) {
      (result as any)[key] = val;
    }
  }
  return result;
};
