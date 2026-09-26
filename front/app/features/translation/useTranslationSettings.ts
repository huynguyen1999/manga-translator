import { useCallback, useState } from "react";
import type { TranslationSettings, TranslatorKey } from "@/types";
import { ocrOptions, summaryModelOptions } from "@/config";
import {
  clearSettings,
  loadRememberSettings,
  loadSettings,
  saveRememberSettings,
  saveSettings,
} from "@/utils/localStorage";

export const useTranslationSettings = () => {
  const [detectionResolution, setDetectionResolution] = useState("2048");
  const [textDetector, setTextDetector] = useState("default");
  const [ocr, setOcr] = useState("48px");
  const [renderFont, setRenderFont] = useState("wildwords");
  const [renderTextDirection, setRenderTextDirection] = useState("auto");
  const [letterCase, setLetterCase] = useState<"none" | "uppercase" | "lowercase">("none");
  const [translator, setTranslator] = useState<TranslatorKey>("deepseek");
  const [summaryModel, setSummaryModel] = useState("deepseek-flash");
  const [targetLanguage, setTargetLanguage] = useState("ENG");
  const [translationQuality, setTranslationQuality] = useState<"fast" | "professional">("fast");

  const [inpaintingSize, setInpaintingSize] = useState("2048");
  const [customUnclipRatio, setCustomUnclipRatio] = useState<number>(2.3);
  const [customBoxThreshold, setCustomBoxThreshold] = useState<number>(0.5);
  const [customOcrProb, setCustomOcrProb] = useState<number | undefined>(undefined);
  const [maskDilationOffset, setMaskDilationOffset] = useState<number>(20);
  const [bubbleDetection, setBubbleDetection] = useState(true);
  const [bubbleModel, setBubbleModel] = useState("yolov8m");
  const [inpainter, setInpainter] = useState("default");
  const [colorizer, setColorizer] = useState("none");
  const [colorizeOnly, setColorizeOnly] = useState(false);
  const [colorizationSize, setColorizationSize] = useState("576");
  const [denoiseSigma, setDenoiseSigma] = useState<number>(25);
  const [colorThreshold, setColorThreshold] = useState<number>(31);
  const [upscaler, setUpscaler] = useState("esrgan");
  const [upscaleRatio, setUpscaleRatio] = useState("");
  const [revertUpscaling, setRevertUpscaling] = useState(true);
  const [rememberSettings, setRememberSettings] = useState(true);
  const [translationBatchSize, setTranslationBatchSize] = useState(20);
  const [settingsHydrated, setSettingsHydrated] = useState(false);

  const getCurrentSettings = (): TranslationSettings => ({
    detectionResolution,
    textDetector,
    ocr,
    renderFont,
    renderTextDirection,
    letterCase,
    uppercase: letterCase === "uppercase",
    lowercase: letterCase === "lowercase",
    translator,
    targetLanguage,
    translationQuality,
    inpaintingSize,
    customUnclipRatio,
    customBoxThreshold,
    customOcrProb,
    ocrMinConfidence: customOcrProb,
    maskDilationOffset,
    bubbleDetection,
    bubbleModel,
    inpainter,
    colorizer,
    colorizeOnly,
    colorizationSize,
    denoiseSigma,
    colorThreshold,
    upscaler,
    upscaleRatio: upscaleRatio ? Number(upscaleRatio) : null,
    revertUpscaling: Boolean(upscaleRatio) && revertUpscaling,
    translationBatchSize,
  });

  const hydrateSettings = () => {
    const savedSettings = loadSettings();
    const shouldRememberSettings = savedSettings.rememberSettings ?? loadRememberSettings();
    setRememberSettings(shouldRememberSettings);
    if (shouldRememberSettings && savedSettings.detectionResolution) setDetectionResolution(savedSettings.detectionResolution);
    if (shouldRememberSettings && savedSettings.textDetector) setTextDetector(savedSettings.textDetector);
    if (shouldRememberSettings && savedSettings.ocr && ocrOptions.some((option) => option.value === savedSettings.ocr)) setOcr(savedSettings.ocr);
    if (shouldRememberSettings && savedSettings.renderFont) setRenderFont(savedSettings.renderFont);
    if (shouldRememberSettings && savedSettings.renderTextDirection) setRenderTextDirection(savedSettings.renderTextDirection);
    if (shouldRememberSettings && savedSettings.letterCase) {
      setLetterCase(savedSettings.letterCase);
    } else if (shouldRememberSettings && savedSettings.uppercase) {
      setLetterCase("uppercase");
    } else if (shouldRememberSettings && savedSettings.lowercase) {
      setLetterCase("lowercase");
    }
    if (shouldRememberSettings && savedSettings.translator) {
      const savedTranslator = savedSettings.translator;
      if (
        (savedTranslator === "youdao" && !savedSettings.migratedDefaultTranslator) ||
        ((savedTranslator as string) === "offline" && !savedSettings.migratedDefaultSugoi) ||
        ((savedTranslator as string) === "qwen2" && !savedSettings.migratedDefaultSugoi) ||
        (savedTranslator === "sugoi" && !savedSettings.migratedDefaultGemini)
      ) {
        setTranslator("deepseek");
      } else {
        setTranslator(savedTranslator);
      }
    }
    if (shouldRememberSettings && savedSettings.summaryModel && summaryModelOptions.some((option) => option.value === savedSettings.summaryModel)) {
      setSummaryModel(savedSettings.summaryModel);
    }
    if (shouldRememberSettings && savedSettings.targetLanguage) {
      if (savedSettings.targetLanguage === "CHS" && !savedSettings.migratedDefaultTargetLang) {
        setTargetLanguage("ENG");
      } else {
        setTargetLanguage(savedSettings.targetLanguage);
      }
    }
    if (shouldRememberSettings && savedSettings.translationQuality) setTranslationQuality(savedSettings.translationQuality);
    if (shouldRememberSettings && savedSettings.inpaintingSize) setInpaintingSize(savedSettings.inpaintingSize);
    if (shouldRememberSettings && savedSettings.customUnclipRatio !== undefined) setCustomUnclipRatio(savedSettings.customUnclipRatio);
    if (shouldRememberSettings && savedSettings.customBoxThreshold !== undefined) setCustomBoxThreshold(savedSettings.customBoxThreshold);
    if (shouldRememberSettings && savedSettings.customOcrProb !== undefined) {
      setCustomOcrProb(savedSettings.customOcrProb);
    } else if (shouldRememberSettings && savedSettings.ocrMinConfidence !== undefined) {
      setCustomOcrProb(savedSettings.ocrMinConfidence);
    }
    if (shouldRememberSettings && savedSettings.maskDilationOffset !== undefined) setMaskDilationOffset(savedSettings.maskDilationOffset);
    if (shouldRememberSettings && savedSettings.bubbleDetection !== undefined) {
      if (!savedSettings.migratedDefaultBubbleDetection) {
        setBubbleDetection(true);
      } else {
        setBubbleDetection(savedSettings.bubbleDetection);
      }
    }
    if (shouldRememberSettings && savedSettings.inpainter) setInpainter(savedSettings.inpainter);
    if (shouldRememberSettings && savedSettings.colorizer) setColorizer(savedSettings.colorizer);
    if (shouldRememberSettings && savedSettings.colorizeOnly !== undefined) setColorizeOnly(savedSettings.colorizeOnly);
    if (shouldRememberSettings && savedSettings.colorizationSize) setColorizationSize(savedSettings.colorizationSize);
    if (shouldRememberSettings && savedSettings.denoiseSigma !== undefined) setDenoiseSigma(savedSettings.denoiseSigma);
    if (shouldRememberSettings && savedSettings.colorThreshold !== undefined) setColorThreshold(savedSettings.colorThreshold);
    if (shouldRememberSettings && savedSettings.upscaler) setUpscaler(savedSettings.upscaler);
    if (shouldRememberSettings && savedSettings.upscaleRatio !== undefined) {
      setUpscaleRatio(savedSettings.upscaleRatio == null ? "" : String(savedSettings.upscaleRatio));
    }
    if (shouldRememberSettings && savedSettings.revertUpscaling !== undefined) {
      setRevertUpscaling(savedSettings.revertUpscaling);
    }
    if (shouldRememberSettings && savedSettings.translationBatchSize !== undefined) {
      setTranslationBatchSize(Math.min(100, Math.max(1, savedSettings.translationBatchSize)));
    }
    setSettingsHydrated(true);
  };

  const persistSettings = useCallback(() => {
    if (!settingsHydrated) return;
    saveRememberSettings(rememberSettings);
    if (!rememberSettings) {
      clearSettings();
      return;
    }
    const settings: TranslationSettings = {
      rememberSettings: true,
      detectionResolution,
      textDetector,
      ocr,
      renderTextDirection,
      letterCase,
      uppercase: letterCase === "uppercase",
      lowercase: letterCase === "lowercase",
      translator,
      summaryModel,
      targetLanguage,
      translationQuality,
      inpaintingSize,
      customUnclipRatio,
      customBoxThreshold,
      customOcrProb,
      ocrMinConfidence: customOcrProb,
      maskDilationOffset,
      bubbleDetection,
      bubbleModel,
      inpainter,
      colorizer,
      colorizeOnly,
      colorizationSize,
      denoiseSigma,
      colorThreshold,
      upscaler,
      upscaleRatio: upscaleRatio ? Number(upscaleRatio) : null,
      revertUpscaling: Boolean(upscaleRatio) && revertUpscaling,
      translationBatchSize,
      migratedDefaultTranslator: true,
      migratedDefaultQwen2: true,
      migratedDefaultSugoi: true,
      migratedDefaultGemini: true,
      migratedDefaultBubbleDetection: true,
      migratedDefaultTargetLang: true,
    };
    saveSettings(settings);
  }, [
    detectionResolution,
    textDetector,
    ocr,
    renderTextDirection,
    letterCase,
    translator,
    summaryModel,
    targetLanguage,
    translationQuality,
    inpaintingSize,
    customUnclipRatio,
    customBoxThreshold,
    customOcrProb,
    maskDilationOffset,
    bubbleDetection,
    bubbleModel,
    inpainter,
    colorizer,
    colorizeOnly,
    colorizationSize,
    denoiseSigma,
    colorThreshold,
    upscaler,
    upscaleRatio,
    revertUpscaling,
    translationBatchSize,
    rememberSettings,
    settingsHydrated,
  ]);

  return {
    detectionResolution,
    setDetectionResolution,
    textDetector,
    setTextDetector,
    ocr,
    setOcr,
    renderFont,
    setRenderFont,
    renderTextDirection,
    setRenderTextDirection,
    letterCase,
    setLetterCase,
    translator,
    setTranslator,
    summaryModel,
    setSummaryModel,
    targetLanguage,
    setTargetLanguage,
    translationQuality,
    setTranslationQuality,
    inpaintingSize,
    setInpaintingSize,
    customUnclipRatio,
    setCustomUnclipRatio,
    customBoxThreshold,
    setCustomBoxThreshold,
    customOcrProb,
    setCustomOcrProb,
    maskDilationOffset,
    setMaskDilationOffset,
    bubbleDetection,
    setBubbleDetection,
    bubbleModel,
    setBubbleModel,
    inpainter,
    setInpainter,
    colorizer,
    setColorizer,
    colorizeOnly,
    setColorizeOnly,
    colorizationSize,
    setColorizationSize,
    denoiseSigma,
    setDenoiseSigma,
    colorThreshold,
    setColorThreshold,
    upscaler,
    setUpscaler,
    upscaleRatio,
    setUpscaleRatio,
    revertUpscaling,
    setRevertUpscaling,
    rememberSettings,
    setRememberSettings,
    translationBatchSize,
    setTranslationBatchSize,
    getCurrentSettings,
    hydrateSettings,
    persistSettings,
  };
};
