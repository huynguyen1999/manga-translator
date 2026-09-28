import assert from "node:assert/strict";
import type { TranslationSettings } from "@/types";
import {
  isSettingsModified,
  sanitizeSettingsForPreset,
} from "./presetUtils";
import { applyPresetSettings, type PresetSetters } from "./applyPresetSettings";

const baseSettings: TranslationSettings = {
  detectionResolution: "2048",
  textDetector: "default",
  ocr: "48px",
  renderFont: "wildwords",
  renderTextDirection: "auto",
  letterCase: "uppercase",
  uppercase: true,
  lowercase: false,
  translator: "deepseek",
  summaryModel: "deepseek-flash",
  targetLanguage: "ENG",
  translationQuality: "fast",
  inpaintingSize: "2048",
  customUnclipRatio: 2.3,
  customBoxThreshold: 0.5,
  maskDilationOffset: 20,
  bubbleDetection: true,
  bubbleModel: "shadowb_manga109",
  bubbleConfidence: 0.25,
  inpainter: "default",
  colorizer: "none",
  colorizeOnly: false,
  colorizationSize: "576",
  denoiseSigma: 25,
  colorThreshold: 31,
  upscaler: "esrgan",
  upscaleRatio: null,
  revertUpscaling: false,
  translationBatchSize: 20,
};

// 1. isSettingsModified tests
assert.equal(
  isSettingsModified(baseSettings, {
    targetLanguage: "ENG",
    translator: "deepseek",
    renderFont: "wildwords",
  }),
  false,
  "Should return false when all preset keys match current settings",
);

assert.equal(
  isSettingsModified(baseSettings, {
    targetLanguage: "JPN",
    translator: "deepseek",
  }),
  true,
  "Should return true when targetLanguage is different",
);

assert.equal(
  isSettingsModified(baseSettings, {
    colorizer: "mc2",
  }),
  true,
  "Should return true when colorizer is different",
);

assert.equal(
  isSettingsModified(baseSettings, {
    upscaleRatio: null,
  }),
  false,
  "Should handle null and empty string normalization for upscaleRatio",
);

assert.equal(
  isSettingsModified(baseSettings, {
    customBoxThreshold: "0.5" as any,
    translationBatchSize: "20" as any,
  }),
  false,
  "Should normalize numeric strings to numbers to avoid false positive modified badges",
);

assert.equal(
  isSettingsModified(
    { ...baseSettings, upscaleRatio: 2 },
    { upscaleRatio: "2" as any },
  ),
  false,
  "Should treat numeric 2 and string '2' as equal for upscaleRatio",
);

// 2. sanitizeSettingsForPreset tests
const sanitized = sanitizeSettingsForPreset({
  ...baseSettings,
  rememberSettings: true,
  migratedDefaultBubbleDetection: true,
} as any);

assert.equal(sanitized.targetLanguage, "ENG");
assert.equal(sanitized.translator, "deepseek");
assert.equal((sanitized as any).rememberSettings, undefined);
assert.equal((sanitized as any).migratedDefaultBubbleDetection, undefined);

// 3. applyPresetSettings tests
const applied: Record<string, any> = {};
const mockSetters: PresetSetters = {
  setDetectionResolution: (val) => { applied.detectionResolution = val; },
  setTextDetector: (val) => { applied.textDetector = val; },
  setOcr: (val) => { applied.ocr = val; },
  setRenderFont: (val) => { applied.renderFont = val; },
  setRenderTextDirection: (val) => { applied.renderTextDirection = val; },
  setLetterCase: (val) => { applied.letterCase = val; },
  setTranslator: (val) => { applied.translator = val; },
  setSummaryModel: (val) => { applied.summaryModel = val; },
  setTargetLanguage: (val) => { applied.targetLanguage = val; },
  setTranslationQuality: (val) => { applied.translationQuality = val; },
  setInpaintingSize: (val) => { applied.inpaintingSize = val; },
  setCustomUnclipRatio: (val) => { applied.customUnclipRatio = val; },
  setCustomBoxThreshold: (val) => { applied.customBoxThreshold = val; },
  setCustomOcrProb: (val) => { applied.customOcrProb = val; },
  setMaskDilationOffset: (val) => { applied.maskDilationOffset = val; },
  setBubbleDetection: (val) => { applied.bubbleDetection = val; },
  setBubbleConfidence: (val) => { applied.bubbleConfidence = val; },
  setInpainter: (val) => { applied.inpainter = val; },
  setColorizer: (val) => { applied.colorizer = val; },
  setColorizeOnly: (val) => { applied.colorizeOnly = val; },
  setColorizationSize: (val) => { applied.colorizationSize = val; },
  setDenoiseSigma: (val) => { applied.denoiseSigma = val; },
  setColorThreshold: (val) => { applied.colorThreshold = val; },
  setUpscaler: (val) => { applied.upscaler = val; },
  setUpscaleRatio: (val) => { applied.upscaleRatio = val; },
  setRevertUpscaling: (val) => { applied.revertUpscaling = val; },
  setTranslationBatchSize: (val) => { applied.translationBatchSize = val; },
};

applyPresetSettings(
  {
    targetLanguage: "CHS",
    translator: "sugoi",
    colorizer: "mc2",
    upscaleRatio: 2,
  },
  mockSetters,
);

assert.equal(applied.targetLanguage, "CHS");
assert.equal(applied.translator, "sugoi");
assert.equal(applied.colorizer, "mc2");
assert.equal(applied.upscaleRatio, "2");
