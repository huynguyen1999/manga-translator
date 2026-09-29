import React, { useState } from "react";
import { Icon } from "@iconify/react";
import type { TranslatorKey } from "@/types";
import { validTranslators } from "@/types";
import { getTranslatorName, getTranslatorGroup } from "@/utils/getTranslatorName";
import { languageOptions, fontOptions, summaryModelOptions, storyAnalysisModelOptions } from "@/config";
import { LabeledSelect } from "@/components/LabeledSelect";
import { LabeledInput } from "@/components/LabeledInput";
import { OptionsEnhancements } from "@/features/studio/options/OptionsEnhancements";
import { OptionsAdvancedPipeline } from "@/features/studio/options/OptionsAdvancedPipeline";
import type { OptionsPanelProps } from "@/features/studio/options/OptionsTypes";
import { PresetToolbar } from "@/features/studio/presets/PresetToolbar";
import { usePresets } from "@/features/studio/presets/usePresets";
import type { TranslationSettings } from "@/types";

export type Props = OptionsPanelProps;
export type { OptionsPanelProps };

const BATCH_TRANSLATORS: TranslatorKey[] = [
  "deepseek",
  "gemini",
  "openai",
  "groq",
  "openrouter", "tokenharbor",
  "custom_openai",
  "sakura",
  "deepl",
];

export const OptionsPanel: React.FC<OptionsPanelProps> = ({
  detectionResolution,
  textDetector,
  ocr,
  renderFont,
  renderTextDirection,
  letterCase,
  translator,
  summaryModel,
  targetLanguage,
  translationQuality,
  draftTranslator,
  inpaintingSize,
  customUnclipRatio,
  customBoxThreshold,
  customOcrProb,
  maskDilationOffset,
  bubbleDetection,
  bubbleConfidence,
  inpainter,
  colorizer,
  colorizeOnly,
  colorizationSize,
  denoiseSigma,
  colorThreshold,
  upscaler,
  upscaleRatio,
  revertUpscaling,
  rememberSettings,
  translationBatchSize,
  setDetectionResolution,
  setTextDetector,
  setOcr,
  setRenderFont,
  setRenderTextDirection,
  setLetterCase,
  setTranslator,
  setSummaryModel,
  setTargetLanguage,
  setTranslationQuality,
  setDraftTranslator,
  setInpaintingSize,
  setCustomUnclipRatio,
  setCustomBoxThreshold,
  setCustomOcrProb,
  setMaskDilationOffset,
  setBubbleDetection,
  setBubbleConfidence,
  setInpainter,
  setColorizer,
  setColorizeOnly,
  setColorizationSize,
  setDenoiseSigma,
  setColorThreshold,
  setUpscaler,
  setUpscaleRatio,
  setRevertUpscaling,
  setRememberSettings,
  setTranslationBatchSize,
}) => {
  const [showAdvanced, setShowAdvanced] = useState(false);

  const resetDefaults = () => {
    setDetectionResolution("2560");
    setTextDetector("default");
    setOcr("48px");
    setRenderFont("wildwords");
    setRenderTextDirection("auto");
    setTranslator("deepseek");
    setSummaryModel("deepseek-flash");
    setTargetLanguage("ENG");
    setTranslationQuality("fast");
    setInpaintingSize("2048");
    setCustomUnclipRatio(2.3);
    setCustomBoxThreshold(0.45);
    setCustomOcrProb(undefined);
    setMaskDilationOffset(30);
    setBubbleDetection(false);
    setBubbleConfidence(0.25);
    setInpainter("default");
    setColorizer("none");
    setColorizeOnly(false);
    setColorizationSize("576");
    setDenoiseSigma(25);
    setColorThreshold(31);
    setUpscaler("esrgan");
    setUpscaleRatio("");
    setRevertUpscaling(true);
    setTranslationBatchSize(20);
  };

  const isBatchTranslator = BATCH_TRANSLATORS.includes(translator);

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
    summaryModel,
    targetLanguage,
    translationQuality,
    inpaintingSize,
    customUnclipRatio,
    customBoxThreshold,
    customOcrProb,
    maskDilationOffset,
    bubbleDetection,
    bubbleConfidence,
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

  const presetManager = usePresets({
    getCurrentSettings,
    setters: {
      setDetectionResolution,
      setTextDetector,
      setOcr,
      setRenderFont,
      setRenderTextDirection,
      setLetterCase,
      setTranslator,
      setSummaryModel,
      setTargetLanguage,
      setTranslationQuality,
      setInpaintingSize,
      setCustomUnclipRatio,
      setCustomBoxThreshold,
      setCustomOcrProb,
      setMaskDilationOffset,
      setBubbleDetection,
      setBubbleConfidence,
      setInpainter,
      setColorizer,
      setColorizeOnly,
      setColorizationSize,
      setDenoiseSigma,
      setColorThreshold,
      setUpscaler,
      setUpscaleRatio,
      setRevertUpscaling,
      setTranslationBatchSize,
    },
    autoHydrateDefault: true,
  });

  return (
    <div className="space-y-4 rounded-xl border border-zinc-200/80 bg-white p-3.5 shadow-xs transition-colors dark:border-zinc-800/80 dark:bg-zinc-900/70 sm:p-4.5">
      {/* Tier 1: Minimal Header / Action Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-100 pb-2.5 dark:border-zinc-800/80">
        <div className="flex items-center space-x-2">
          <div className="flex h-5 w-5 items-center justify-center rounded-md bg-indigo-50 text-indigo-600 dark:bg-indigo-950/60 dark:text-indigo-400">
            <Icon icon="carbon:settings-adjust" className="h-3.5 w-3.5" />
          </div>
          <span className="text-xs font-bold uppercase tracking-wider text-zinc-800 dark:text-zinc-200">
            Translation & Processing Pipeline
          </span>
          <span className="hidden sm:inline-flex items-center rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
            {translationQuality === "professional" ? "Pro Localization" : "Fast Mode"}
          </span>
        </div>

        <PresetToolbar
          presets={presetManager.presets}
          activePreset={presetManager.activePreset}
          activePresetId={presetManager.activePresetId}
          isModified={presetManager.isModified}
          loading={presetManager.loading}
          onSelectPreset={presetManager.selectPreset}
          onSaveAsNew={presetManager.saveCurrentAsNew}
          onUpdateActive={presetManager.updateActivePreset}
          onRevertActive={presetManager.revertActivePreset}
          onSetDefault={presetManager.setDefaultById}
          onDeletePreset={presetManager.deletePresetById}
          onRenamePreset={presetManager.renamePreset}
        />

        <div className="flex items-center space-x-4">
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200">
            <input
              type="checkbox"
              checked={rememberSettings}
              onChange={(e) => setRememberSettings(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-zinc-300 text-indigo-600 focus:ring-indigo-500 dark:border-zinc-600"
            />
            <span title="Keep these options after refreshing the page">Remember settings</span>
          </label>
          <button
            type="button"
            onClick={() => {
              resetDefaults();
              presetManager.clearActivePreset();
            }}
            className="flex items-center space-x-1 text-xs text-zinc-500 transition-colors hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200"
          >
            <Icon icon="carbon:reset" className="h-3.5 w-3.5" />
            <span>Reset</span>
          </button>
        </div>
      </div>

      {/* Tier 2: Core Workspace (Responsive 2-Card Layout) */}
      <div className="grid grid-cols-1 gap-3.5 lg:grid-cols-12">
        {/* Left Card: Translation & AI Configuration (7 Cols) */}
        <div className="rounded-lg border border-zinc-200/60 bg-zinc-50/50 p-3.5 dark:border-zinc-800/60 dark:bg-zinc-800/25 lg:col-span-7 flex flex-col justify-between space-y-3">
          <div className="flex items-center justify-between border-b border-zinc-200/40 pb-2 dark:border-zinc-800/40">
            <div className="flex items-center space-x-1.5 text-xs font-bold uppercase tracking-wider text-indigo-600 dark:text-indigo-400">
              <Icon icon="carbon:translate" className="h-3.5 w-3.5" />
              <span>Translation & AI Engine</span>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <LabeledSelect
              id="targetLanguage"
              label="Target Language"
              icon="carbon:language"
              title="Target language"
              value={targetLanguage}
              onChange={(value) =>
                setTargetLanguage(translationQuality === "professional" ? "ENG" : value)
              }
              options={languageOptions}
              tooltip="Language into which text in comic speech bubbles will be translated"
            />

            <LabeledSelect
              id="translator"
              label="Translation Engine"
              icon="carbon:machine-learning-model"
              title="Translator service"
              value={translator}
              onChange={(val) => setTranslator(val as TranslatorKey)}
              options={validTranslators.map((key) => ({
                value: key,
                label: getTranslatorName(key),
                group: getTranslatorGroup(key),
              }))}
              tooltip="Neural translation backend (Offline local models or cloud API providers)"
            />
          </div>

          <div className={`grid grid-cols-1 gap-3 ${isBatchTranslator ? "sm:grid-cols-2" : ""}`}>
            <LabeledSelect
              id="translationQuality"
              label="Translation Quality"
              icon="carbon:translate"
              title="Translation workflow"
              value={translationQuality}
              onChange={(value) => {
                const quality = value as "fast" | "professional";
                setTranslationQuality(quality);
                if (quality === "professional") setTargetLanguage("ENG");
              }}
              options={[
                { value: "fast", label: "Fast" },
                { value: "professional", label: "Professional localization" },
              ]}
              tooltip="Professional LLMs use story and prior-chunk context for each chunk; offline engines translate directly"
            />

            {isBatchTranslator && (
              <LabeledInput
                id="translationBatchSize"
                label="Batch Translation Size"
                icon="carbon:layers"
                title="Maximum pages combined into one AI or DeepL translation request"
                min={1}
                max={100}
                step={1}
                value={translationBatchSize}
                onChange={(val) =>
                  setTranslationBatchSize(Math.min(100, Math.max(1, Number(val) || 1)))
                }
                tooltip="Maximum number of pages combined into a single LLM or DeepL translation request (1–100 pages, default 20)."
              />
            )}
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <LabeledSelect
              id="draftTranslator"
              label="Story Analysis Model"
              icon="carbon:analytics"
              title="Story analysis model"
              value={draftTranslator || ""}
              onChange={(val) => setDraftTranslator?.(val as TranslatorKey | "")}
              options={storyAnalysisModelOptions}
              tooltip="AI model for story boundaries, character voice, and narrative guide in Professional mode."
            />

            <LabeledSelect
              id="summaryModel"
              label="Manga Synopsis Model"
              icon="carbon:document-sentiment"
              title="Manga synopsis model"
              value={summaryModel}
              onChange={setSummaryModel}
              options={summaryModelOptions}
              tooltip="Global model used for generating manga series summaries in the Gallery."
            />
          </div>
        </div>

        {/* Right Card: Typography & Lettering (5 Cols) */}
        <div className="rounded-lg border border-zinc-200/60 bg-zinc-50/50 p-3.5 dark:border-zinc-800/60 dark:bg-zinc-800/25 lg:col-span-5 flex flex-col justify-between space-y-3">
          <div className="flex items-center space-x-1.5 border-b border-zinc-200/40 pb-2 text-xs font-bold uppercase tracking-wider text-amber-600 dark:text-amber-400 dark:border-zinc-800/40">
            <Icon icon="carbon:text-font" className="h-3.5 w-3.5" />
            <span>Typography & Lettering</span>
          </div>

          <LabeledSelect
            id="renderFont"
            label="Font Type"
            icon="carbon:text-font"
            title="Font used for rendering dialogue and sound effects"
            value={renderFont || "wildwords"}
            onChange={setRenderFont}
            options={fontOptions}
            tooltip="Lettering font used for speech bubble rendering. Defaults to Wild Words."
          />

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <LabeledSelect
              id="renderTextDirection"
              label="Text Direction"
              icon="carbon:text-align-left"
              title="Render text orientation"
              value={renderTextDirection}
              onChange={setRenderTextDirection}
              options={[
                { value: "auto", label: "Auto (Detect)" },
                { value: "vertical", label: "Vertical" },
                { value: "horizontal", label: "Horizontal" },
              ]}
              tooltip="Orientation of translated font rendering inside speech bubbles"
            />

            <LabeledSelect
              id="letterCase"
              label="Lettering Case"
              icon="carbon:character-whole"
              title="Lettering casing style"
              value={letterCase}
              onChange={(val) => setLetterCase(val as "none" | "uppercase" | "lowercase")}
              options={[
                { value: "none", label: "Original" },
                { value: "uppercase", label: "ALL CAPS" },
                { value: "lowercase", label: "lowercase" },
              ]}
              tooltip="Turn translated text into ALL CAPS, lowercase, or keep original mixed case for dialogue lettering"
            />
          </div>
        </div>
      </div>

      {/* Tier 3: Visual Enhancements Strip (Colorization & Upscaling) */}
      <OptionsEnhancements
        colorizer={colorizer}
        setColorizer={setColorizer}
        colorizeOnly={colorizeOnly}
        setColorizeOnly={setColorizeOnly}
        colorizationSize={colorizationSize}
        setColorizationSize={setColorizationSize}
        colorThreshold={colorThreshold}
        setColorThreshold={setColorThreshold}
        upscaler={upscaler}
        setUpscaler={setUpscaler}
        upscaleRatio={upscaleRatio}
        setUpscaleRatio={setUpscaleRatio}
        revertUpscaling={revertUpscaling}
        setRevertUpscaling={setRevertUpscaling}
      />

      {/* Tier 4: Collapsible Advanced Engine Parameters */}
      <OptionsAdvancedPipeline
        showAdvanced={showAdvanced}
        setShowAdvanced={setShowAdvanced}
        detectionResolution={detectionResolution}
        setDetectionResolution={setDetectionResolution}
        textDetector={textDetector}
        setTextDetector={setTextDetector}
        ocr={ocr}
        setOcr={setOcr}
        customBoxThreshold={customBoxThreshold}
        setCustomBoxThreshold={setCustomBoxThreshold}
        customUnclipRatio={customUnclipRatio}
        setCustomUnclipRatio={setCustomUnclipRatio}
        customOcrProb={customOcrProb}
        setCustomOcrProb={setCustomOcrProb}
        inpainter={inpainter}
        setInpainter={setInpainter}
        inpaintingSize={inpaintingSize}
        setInpaintingSize={setInpaintingSize}
        maskDilationOffset={maskDilationOffset}
        setMaskDilationOffset={setMaskDilationOffset}
        bubbleDetection={bubbleDetection}
        setBubbleDetection={setBubbleDetection}
        bubbleConfidence={bubbleConfidence}
        setBubbleConfidence={setBubbleConfidence}
      />
    </div>
  );
};
