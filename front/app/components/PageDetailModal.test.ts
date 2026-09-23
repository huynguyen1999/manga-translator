import assert from "node:assert/strict";
import type { FinishedImage } from "@/types";
import type { PipelineRunManifest } from "@/types";
import {
  formatElapsedTime,
  formatTimestamp,
  resolveImageUrls,
  resolveLanguageName,
  resolvePipelineStepSettings,
  resolveStagesToRetry,
  resolveTranslationTiming,
  resolveTranslatorEngine,
  resolveTranslatorModel,
  shouldLoadTranslationArtifacts,
} from "@/components/PageDetailModal";
import { resultFolderFromUrl } from "@/utils/resultPaths";

// 1. Navigation index resolution
function getNextPrevIndices(currentIndex: number, total: number): { next: number; prev: number } {
  if (total <= 1) return { next: currentIndex, prev: currentIndex };
  const prev = currentIndex <= 0 ? total - 1 : currentIndex - 1;
  const next = currentIndex >= total - 1 ? 0 : currentIndex + 1;
  return { next, prev };
}

// Navigation wrapping
assert.deepEqual(getNextPrevIndices(0, 5), { next: 1, prev: 4 });
assert.deepEqual(getNextPrevIndices(4, 5), { next: 0, prev: 3 });
assert.deepEqual(getNextPrevIndices(2, 5), { next: 3, prev: 1 });
assert.deepEqual(getNextPrevIndices(0, 1), { next: 0, prev: 0 });

// 2. Image URL derivation logic
const mockImg: FinishedImage = {
  id: "page-1",
  originalName: "001.png",
  result: "/result/folder123/final.png",
  folder: "folder123",
  bubbleMaskUrl: "/result/folder123/bubble_mask.png",
  finishedAt: new Date(),
  settings: { translator: "none" },
};

const urls = resolveImageUrls(mockImg, (p) => `http://localhost:5000${p}`);
assert.equal(urls.resultUrl, "/result/folder123/final.png");
assert.equal(urls.inpaintedUrl, "http://localhost:5000/result/folder123/inpainted.jpg");
assert.equal(urls.bubbleMaskUrl, "http://localhost:5000/result/folder123/bubble_mask.png");
assert.equal(urls.originalUrl, "http://localhost:5000/result/folder123/input.png");

const urlOnlyImage = { ...mockImg, folder: undefined, result: "/result/folder123/final.jpg" };
const inferredUrls = resolveImageUrls(urlOnlyImage, (p) => `http://localhost:5000${p}`);
assert.equal(inferredUrls.inpaintedUrl, "http://localhost:5000/result/folder123/inpainted.jpg");
assert.equal(inferredUrls.originalUrl, "http://localhost:5000/result/folder123/input.png");
assert.equal(resultFolderFromUrl("/result/folder%20123/inpainted.jpg"), "folder 123");

const customInputUrlImage: FinishedImage = {
  ...mockImg,
  inputUrl: "/custom/uploaded/original.png",
};
const customUrls = resolveImageUrls(customInputUrlImage, (p) => `http://localhost:5000${p}`);
assert.equal(customUrls.originalUrl, "http://localhost:5000/custom/uploaded/original.png");

const originalTypeImage: FinishedImage = {
  ...mockImg,
  sourceType: "original",
  result: "/result/folder123/raw.png",
};
const originalUrls = resolveImageUrls(originalTypeImage, (p) => `http://localhost:5000${p}`);
assert.equal(originalUrls.originalUrl, "/result/folder123/raw.png");

// 3. Pipeline duration formatting
assert.equal(formatElapsedTime(842), "842 ms");
assert.equal(formatElapsedTime(1240), "1.24 s");
assert.equal(formatElapsedTime(undefined), "—");

// 4. Translation detail resolution
assert.equal(resolveTranslatorEngine("openrouter"), "OpenRouter");
assert.equal(resolveTranslatorEngine("gemini"), "Gemini");
assert.equal(resolveTranslatorModel(
  "groq",
  { translator: "groq", translatorModel: "captured-model" },
  null,
  { translator: { model: "detail-model" } },
), "detail-model");
assert.equal(resolveTranslatorModel("openrouter", { translator: "openrouter" }, null, null), "deepseek/deepseek-v4-flash-0731");

// 5. Timestamp and total duration resolution
const manifest: PipelineRunManifest = {
  version: 1,
  kind: "pipeline-run" as const,
  folder: "page-1",
  status: "completed" as const,
  createdAt: "2026-09-16T01:02:03.000Z",
  updatedAt: "2026-09-16T01:02:08.500Z",
  source: { filename: "001.png", width: 1, height: 1, mode: "RGB" },
  config: {},
  stages: [
    { id: "detection", label: "Detection", status: "completed", durationMs: 1200 },
    { id: "ocr", label: "OCR", status: "completed", durationMs: 800 },
    { id: "translation", label: "Translation", status: "completed", durationMs: 2500 },
    { id: "rendering", label: "Rendering", status: "completed", durationMs: 1000 },
  ],
};
assert.equal(formatTimestamp(new Date(2026, 8, 16, 1, 2, 3)), "01:02:03");
assert.deepEqual(resolveTranslationTiming(manifest), {
  startAt: manifest.createdAt,
  endAt: manifest.updatedAt,
  durationMs: 5500,
});
const fullRetryStages = [
  { id: "input", label: "Input", status: "completed", dependsOn: [] },
  { id: "colorization", label: "Colorization", status: "completed", dependsOn: ["input"] },
  { id: "upscaling", label: "Upscaling", status: "completed", dependsOn: ["input", "colorization"] },
  { id: "detection", label: "Detection", status: "completed", dependsOn: ["upscale"] },
  { id: "ocr", label: "OCR", status: "completed", dependsOn: ["detection"] },
  { id: "bubble_detection", label: "Bubbles", status: "completed", dependsOn: ["upscale"] },
  { id: "textline_merge", label: "Grouping", status: "completed", dependsOn: ["ocr", "bubble_detection"] },
  { id: "translation", label: "Translation", status: "completed", dependsOn: ["text_grouping"] },
  { id: "mask_generation", label: "Mask", status: "completed", dependsOn: ["detection", "ocr", "bubble_detection", "text_grouping"] },
  { id: "layout", label: "Layout", status: "completed", dependsOn: ["translation", "bubble_detection", "text_grouping"] },
  { id: "inpainting", label: "Inpainting", status: "completed", dependsOn: ["mask_generation"] },
  { id: "rendering", label: "Rendering", status: "completed", dependsOn: ["layout", "inpainting"] },
];
assert.deepEqual(resolveStagesToRetry(fullRetryStages, "translation"), [
  "Translation", "Layout", "Rendering",
]);
assert.deepEqual(resolveStagesToRetry(fullRetryStages, "ocr"), [
  "OCR", "Grouping", "Translation", "Mask", "Layout", "Inpainting", "Rendering",
]);
assert.deepEqual(resolveStagesToRetry([
  { id: "input", label: "Input", status: "completed", dependsOn: [] },
  { id: "ocr", label: "OCR", status: "completed", dependsOn: ["detection"] },
  { id: "bubble_detection", label: "Bubbles", status: "skipped", dependsOn: ["upscale"] },
  { id: "text_grouping", label: "Grouping", status: "completed", dependsOn: ["ocr", "bubble_detection"] },
  { id: "translation", label: "Translation", status: "failed", dependsOn: ["text_grouping"] },
], "ocr"), ["OCR", "Grouping", "Translation"]);

// 5b. Timing resolution with image startedAt and explicit durationMs
assert.deepEqual(
  resolveTranslationTiming(
    null,
    "2026-09-16T01:02:08.500Z",
    "2026-09-16T01:02:03.000Z",
    5500,
  ),
  {
    startAt: "2026-09-16T01:02:03.000Z",
    endAt: "2026-09-16T01:02:08.500Z",
    durationMs: 5500,
  },
);

// 5c. Timing resolution from stage durations when manifest has no top-level timestamps
const stageOnlyManifest: PipelineRunManifest = {
  version: 1,
  kind: "pipeline-run" as const,
  folder: "page-2",
  status: "completed" as const,
  source: { filename: "002.png", width: 1, height: 1, mode: "RGB" },
  config: {},
  stages: [
    { id: "detection", label: "Detection", status: "completed", durationMs: 1500 },
    { id: "ocr", label: "OCR", status: "completed", durationMs: 500 },
  ],
};
assert.deepEqual(resolveTranslationTiming(stageOnlyManifest), {
  startAt: null,
  endAt: null,
  durationMs: 2000,
});

// 6. Original pages omit pipeline artifacts and sidebar data
assert.equal(shouldLoadTranslationArtifacts("original"), false);
assert.equal(shouldLoadTranslationArtifacts("translated"), true);

// 7. Language name resolution
assert.equal(resolveLanguageName("ENG"), "English (ENG)");
assert.equal(resolveLanguageName("JPN"), "日本語 (JPN)");
assert.equal(resolveLanguageName("CHS"), "简体中文 (CHS)");
assert.equal(resolveLanguageName(undefined), undefined);

// 8. Pipeline step settings resolution from flat settings
const flatSettings = {
  textDetector: "ctd",
  detectionResolution: "2048",
  customBoxThreshold: 0.6,
  customUnclipRatio: 2.1,
  ocr: "mocr",
  customOcrProb: 0.25,
  useMocrMerge: true,
  bubbleDetection: true,
  bubbleModel: "manga109",
  bubbleConfidence: 0.35,
  bubbleMaskThreshold: 0.55,
  inpainter: "lama_mpe",
  inpaintingSize: "2048",
  inpaintingPrecision: "bf16",
  maskDilationOffset: 25,
  renderer: "manga2eng",
  renderTextDirection: "horizontal",
  renderAlignment: "center",
  renderFont: "WildWords",
  letterCase: "uppercase" as const,
  translator: "gemini" as const,
  geminiModel: "gemini-1.5-flash-002",
  targetLanguage: "ENG",
  colorizer: "mc2",
  colorizationSize: "576",
  denoiseSigma: 20,
  colorThreshold: 28,
  upscaler: "4xultrasharp",
  upscaleRatio: 4,
  revertUpscaling: true,
};

const resolvedFlat = resolvePipelineStepSettings(flatSettings, null, {
  translator: {
    name: "gemini",
    model: "gemini-1.5-flash-002",
    sourceLanguage: "auto",
    targetLanguage: "ENG",
  },
});

assert.equal(resolvedFlat.detection.detector, "ctd");
assert.equal(resolvedFlat.detection.detectorLabel, "Comic Text Detector");
assert.equal(resolvedFlat.detection.resolution, "2048 px");
assert.equal(resolvedFlat.detection.boxThreshold, 0.6);
assert.equal(resolvedFlat.detection.unclipRatio, 2.1);

assert.equal(resolvedFlat.ocr.model, "mocr");
assert.equal(resolvedFlat.ocr.modelLabel, "Manga OCR");
assert.equal(resolvedFlat.ocr.prob, 0.25);
assert.equal(resolvedFlat.ocr.useMocrMerge, true);

assert.equal(resolvedFlat.bubbleDetection.enabled, true);
assert.equal(resolvedFlat.bubbleDetection.model, "manga109");
assert.equal(resolvedFlat.bubbleDetection.confidence, 0.35);
assert.equal(resolvedFlat.bubbleDetection.maskThreshold, 0.55);

assert.equal(resolvedFlat.inpainting.inpainter, "lama_mpe");
assert.equal(resolvedFlat.inpainting.inpainterLabel, "LaMa MPE");
assert.equal(resolvedFlat.inpainting.size, "2048 px");
assert.equal(resolvedFlat.inpainting.precision, "BF16");
assert.equal(resolvedFlat.inpainting.maskDilation, 25);

assert.equal(resolvedFlat.rendering.renderer, "manga2eng");
assert.equal(resolvedFlat.rendering.rendererLabel, "Manga2Eng");
assert.equal(resolvedFlat.rendering.direction, "horizontal");
assert.equal(resolvedFlat.rendering.alignment, "center");
assert.equal(resolvedFlat.rendering.font, "WildWords");
assert.equal(resolvedFlat.rendering.letterCase, "uppercase");
assert.equal(resolvedFlat.rendering.letterCaseLabel, "ALL CAPS");

assert.equal(resolvedFlat.translation.engine, "Gemini");
assert.equal(resolvedFlat.translation.model, "gemini-1.5-flash-002");
assert.equal(resolvedFlat.translation.sourceLanguage, "Auto");
assert.equal(resolvedFlat.translation.targetLanguage, "English (ENG)");

assert.equal(resolvedFlat.colorization?.enabled, true);
assert.equal(resolvedFlat.colorization?.colorizerLabel, "MC2 (Manga Colorization V2)");
assert.equal(resolvedFlat.colorization?.size, "576 px");

assert.equal(resolvedFlat.upscaling?.enabled, true);
assert.equal(resolvedFlat.upscaling?.upscalerLabel, "4x UltraSharp");
assert.equal(resolvedFlat.upscaling?.ratio, "4x");

// 9. Pipeline step settings resolution from nested manifest config
const nestedManifest: PipelineRunManifest = {
  version: 1,
  kind: "pipeline-run" as const,
  folder: "page-nested",
  status: "completed" as const,
  source: { filename: "page.png", width: 800, height: 1200, mode: "RGB" },
  config: {
    detector: {
      detector: "paddle",
      detection_size: 2560,
      box_threshold: 0.5,
      unclip_ratio: 2.3,
    },
    ocr: {
      ocr: "48px_ctc",
      use_mocr_merge: false,
    },
    bubble_detection: {
      enabled: false,
    },
    inpainter: {
      inpainter: "lama_large",
      inpainting_size: 2048,
    },
    mask_dilation_offset: 30,
    render: {
      renderer: "default",
      direction: "auto",
      alignment: "auto",
    },
    translator: {
      translator: "deepseek",
      source_lang: "JPN",
      target_lang: "CHS",
    },
    colorizer: {
      colorizer: "none",
    },
    upscale: {
      upscaler: "esrgan",
      upscale_ratio: null,
    },
  },
  stages: [],
};

const resolvedNested = resolvePipelineStepSettings(null, nestedManifest, null);
assert.equal(resolvedNested.detection.detector, "paddle");
assert.equal(resolvedNested.detection.detectorLabel, "PaddleOCR");
assert.equal(resolvedNested.detection.resolution, "2560 px");
assert.equal(resolvedNested.ocr.model, "48px_ctc");
assert.equal(resolvedNested.ocr.modelLabel, "48px CTC");
assert.equal(resolvedNested.bubbleDetection.enabled, false);
assert.equal(resolvedNested.inpainting.inpainter, "lama_large");
assert.equal(resolvedNested.inpainting.maskDilation, 30);
assert.equal(resolvedNested.rendering.renderer, "default");
assert.equal(resolvedNested.translation.engine, "DeepSeek");
assert.equal(resolvedNested.translation.sourceLanguage, "日本語 (JPN)");
assert.equal(resolvedNested.translation.targetLanguage, "简体中文 (CHS)");
assert.equal(resolvedNested.colorization?.enabled, false);
assert.equal(resolvedNested.upscaling?.enabled, false);

console.log("PageDetailModal unit tests passed successfully!");
