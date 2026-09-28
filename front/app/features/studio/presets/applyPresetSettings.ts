import type { TranslationSettings, TranslatorKey } from "@/types";

export interface PresetSetters {
  setDetectionResolution: (val: string) => void;
  setTextDetector: (val: string) => void;
  setOcr: (val: string) => void;
  setRenderFont: (val: string) => void;
  setRenderTextDirection: (val: string) => void;
  setLetterCase: (val: "none" | "uppercase" | "lowercase") => void;
  setTranslator: (val: TranslatorKey) => void;
  setSummaryModel: (val: string) => void;
  setTargetLanguage: (val: string) => void;
  setTranslationQuality: (val: "fast" | "professional") => void;
  setDraftTranslator?: (val: TranslatorKey | "") => void;
  setInpaintingSize: (val: string) => void;
  setCustomUnclipRatio: (val: number) => void;
  setCustomBoxThreshold: (val: number) => void;
  setCustomOcrProb: (val: number | undefined) => void;
  setMaskDilationOffset: (val: number) => void;
  setBubbleDetection: (val: boolean) => void;
  setBubbleModel?: (val: string) => void;
  setBubbleConfidence: (val: number) => void;
  setInpainter: (val: string) => void;
  setColorizer: (val: string) => void;
  setColorizeOnly: (val: boolean) => void;
  setColorizationSize: (val: string) => void;
  setDenoiseSigma: (val: number) => void;
  setColorThreshold: (val: number) => void;
  setUpscaler: (val: string) => void;
  setUpscaleRatio: (val: string) => void;
  setRevertUpscaling: (val: boolean) => void;
  setTranslationBatchSize: (val: number) => void;
}

export const applyPresetSettings = (
  settings: Partial<TranslationSettings>,
  setters: PresetSetters,
): void => {
  if (settings.detectionResolution !== undefined) setters.setDetectionResolution(settings.detectionResolution);
  if (settings.textDetector !== undefined) setters.setTextDetector(settings.textDetector);
  if (settings.ocr !== undefined) setters.setOcr(settings.ocr);
  if (settings.renderFont !== undefined) setters.setRenderFont(settings.renderFont);
  if (settings.renderTextDirection !== undefined) setters.setRenderTextDirection(settings.renderTextDirection);
  if (settings.letterCase !== undefined) setters.setLetterCase(settings.letterCase);
  else if (settings.uppercase) setters.setLetterCase("uppercase");
  else if (settings.lowercase) setters.setLetterCase("lowercase");
  if (settings.translator !== undefined) setters.setTranslator(settings.translator);
  if (settings.summaryModel !== undefined) setters.setSummaryModel(settings.summaryModel);
  if (settings.targetLanguage !== undefined) setters.setTargetLanguage(settings.targetLanguage);
  if (settings.translationQuality !== undefined) setters.setTranslationQuality(settings.translationQuality);
  if (settings.draftTranslator !== undefined && setters.setDraftTranslator) {
    setters.setDraftTranslator(settings.draftTranslator || "");
  }
  if (settings.inpaintingSize !== undefined) setters.setInpaintingSize(settings.inpaintingSize);
  if (settings.customUnclipRatio !== undefined) setters.setCustomUnclipRatio(settings.customUnclipRatio);
  if (settings.customBoxThreshold !== undefined) setters.setCustomBoxThreshold(settings.customBoxThreshold);
  if (settings.customOcrProb !== undefined) setters.setCustomOcrProb(settings.customOcrProb);
  else if (settings.ocrMinConfidence !== undefined) setters.setCustomOcrProb(settings.ocrMinConfidence);
  if (settings.maskDilationOffset !== undefined) setters.setMaskDilationOffset(settings.maskDilationOffset);
  if (settings.bubbleDetection !== undefined) setters.setBubbleDetection(settings.bubbleDetection);
  if (settings.bubbleModel !== undefined && setters.setBubbleModel) setters.setBubbleModel(settings.bubbleModel);
  if (settings.bubbleConfidence !== undefined) setters.setBubbleConfidence(settings.bubbleConfidence);
  if (settings.inpainter !== undefined) setters.setInpainter(settings.inpainter);
  if (settings.colorizer !== undefined) setters.setColorizer(settings.colorizer);
  if (settings.colorizeOnly !== undefined) setters.setColorizeOnly(settings.colorizeOnly);
  if (settings.colorizationSize !== undefined) setters.setColorizationSize(settings.colorizationSize);
  if (settings.denoiseSigma !== undefined) setters.setDenoiseSigma(settings.denoiseSigma);
  if (settings.colorThreshold !== undefined) setters.setColorThreshold(settings.colorThreshold);
  if (settings.upscaler !== undefined) setters.setUpscaler(settings.upscaler);
  if (settings.upscaleRatio !== undefined) {
    setters.setUpscaleRatio(settings.upscaleRatio == null ? "" : String(settings.upscaleRatio));
  }
  if (settings.revertUpscaling !== undefined) setters.setRevertUpscaling(settings.revertUpscaling);
  if (settings.translationBatchSize !== undefined) setters.setTranslationBatchSize(settings.translationBatchSize);
};
