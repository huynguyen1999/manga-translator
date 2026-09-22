import React, { useState } from "react";
import { Icon } from "@iconify/react";
import type { TranslatorKey } from "@/types";
import { validTranslators } from "@/types";
import { getTranslatorName, getTranslatorGroup } from "@/utils/getTranslatorName";
import {
  languageOptions,
  detectionResolutions,
  ocrOptions,
  textDetectorOptions,
  inpaintingSizes,
  inpainterOptions,
  colorizerOptions,
  colorizationSizes,
  upscalerOptions,
  upscaleRatioOptions,
  summaryModelOptions,
  fontOptions,
} from "@/config";
import { LabeledInput } from "@/components/LabeledInput";
import { LabeledSelect } from "@/components/LabeledSelect";

type Props = {
  detectionResolution: string;
  textDetector: string;
  ocr: string;
  renderFont: string;
  renderTextDirection: string;
  letterCase: "none" | "uppercase" | "lowercase";
  translator: TranslatorKey;
  summaryModel: string;
  targetLanguage: string;
  translationQuality: "fast" | "professional";
  inpaintingSize: string;
  customUnclipRatio: number;
  customBoxThreshold: number;
  customOcrProb?: number;
  maskDilationOffset: number;
  bubbleDetection: boolean;
  inpainter: string;
  colorizer: string;
  colorizeOnly: boolean;
  colorizationSize: string;
  denoiseSigma: number;
  colorThreshold: number;
  upscaler: string;
  upscaleRatio: string;
  revertUpscaling: boolean;
  rememberSettings: boolean;
  translationBatchSize: number;

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
  setInpaintingSize: (val: string) => void;
  setCustomUnclipRatio: (val: number) => void;
  setCustomBoxThreshold: (val: number) => void;
  setCustomOcrProb: (val: number | undefined) => void;
  setMaskDilationOffset: (val: number) => void;
  setBubbleDetection: (val: boolean) => void;
  setInpainter: (val: string) => void;
  setColorizer: (val: string) => void;
  setColorizeOnly: (val: boolean) => void;
  setColorizationSize: (val: string) => void;
  setDenoiseSigma: (val: number) => void;
  setColorThreshold: (val: number) => void;
  setUpscaler: (val: string) => void;
  setUpscaleRatio: (val: string) => void;
  setRevertUpscaling: (val: boolean) => void;
  setRememberSettings: (val: boolean) => void;
  setTranslationBatchSize: (val: number) => void;
};

export const OptionsPanel: React.FC<Props> = ({
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
  inpaintingSize,
  customUnclipRatio,
  customBoxThreshold,
  customOcrProb,
  maskDilationOffset,
  bubbleDetection,
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
  setInpaintingSize,
  setCustomUnclipRatio,
  setCustomBoxThreshold,
  setCustomOcrProb,
  setMaskDilationOffset,
  setBubbleDetection,
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

  return (
    <div className="space-y-5 rounded-xl border border-zinc-200 bg-white p-3 shadow-xs transition-colors dark:border-zinc-800 dark:bg-zinc-900/60 sm:p-5">
      {/* Top Actions Row */}
      <div className="flex items-center justify-between border-b border-zinc-100 pb-3 dark:border-zinc-800">
        <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
          <input
            type="checkbox"
            checked={rememberSettings}
            onChange={(e) => setRememberSettings(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-zinc-300 text-indigo-600 focus:ring-indigo-500 dark:border-zinc-600"
          />
          <span title="Keep these options after refreshing the page">Remember settings across refreshes</span>
        </label>
        <button
          type="button"
          onClick={resetDefaults}
          className="flex items-center space-x-1 text-xs text-zinc-500 transition-colors hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200"
        >
          <Icon icon="carbon:reset" className="h-3.5 w-3.5" />
          <span>Reset defaults</span>
        </button>
      </div>

      {/* Primary 3-Column Settings Grid */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3 lg:grid-cols-4">
        {/* Target Language */}
        <LabeledSelect
          id="targetLanguage"
          label="Target Language"
          icon="carbon:language"
          title="Target language"
          value={targetLanguage}
          onChange={(value) => setTargetLanguage(translationQuality === "professional" ? "ENG" : value)}
          options={languageOptions}
          tooltip="Language into which text in comic speech bubbles will be translated"
        />

        {/* Translation Engine */}
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
          tooltip="Professional mode reads the complete batch, translates sequentially, and performs an editor pass"
        />

        {/* Synopsis Model */}
        <LabeledSelect
          id="summaryModel"
          label="Synopsis Model"
          icon="carbon:document-sentiment"
          title="Model used for manga synopsis jobs"
          value={summaryModel}
          onChange={setSummaryModel}
          options={summaryModelOptions}
          tooltip="Global model used when generating manga synopses. This is separate from image translation."
        />

        {/* Font Type */}
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

        {/* Text Direction */}
        <LabeledSelect
          id="renderTextDirection"
          label="Text Direction"
          icon="carbon:text-align-left"
          title="Render text orientation"
          value={renderTextDirection}
          onChange={setRenderTextDirection}
          options={[
            { value: "auto", label: "Auto (Detect from bubble shape)" },
            { value: "vertical", label: "Vertical (Traditional Manga)" },
            { value: "horizontal", label: "Horizontal (Western / Webtoon)" },
          ]}
          tooltip="Orientation of translated font rendering inside speech bubbles"
        />

        {/* Lettering Case */}
        <LabeledSelect
          id="letterCase"
          label="Lettering Case"
          icon="carbon:character-whole"
          title="Lettering casing style"
          value={letterCase}
          onChange={(val) => setLetterCase(val as "none" | "uppercase" | "lowercase")}
          options={[
            { value: "none", label: "Original (Mixed case)" },
            { value: "uppercase", label: "ALL CAPS (Uppercase)" },
            { value: "lowercase", label: "lowercase" },
          ]}
          tooltip="Turn translated text into ALL CAPS, lowercase, or keep original mixed case for dialogue lettering"
        />
      </div>

      {/* Colorization Row */}
      <div className="flex flex-col items-start justify-between gap-3 rounded-lg border border-zinc-200/60 bg-zinc-50/70 p-3 dark:border-zinc-800/60 dark:bg-zinc-800/40 sm:flex-row sm:items-center sm:gap-4">
        <div className="flex w-full flex-wrap items-center gap-3 sm:w-auto sm:gap-4">
          <div className="flex items-center space-x-2">
            <Icon icon="carbon:color-palette" className="h-4 w-4 text-purple-600 dark:text-purple-400" />
            <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">Colorization:</span>
          </div>
          <select
            id="colorizerSelect"
            value={colorizer}
            onChange={(e) => {
              const val = e.target.value;
              setColorizer(val);
              if (val === "none") setColorizeOnly(false);
            }}
            className="rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2.5 py-1 text-xs font-medium text-zinc-800 dark:text-zinc-200 focus:border-purple-500 focus:outline-none"
          >
            {colorizerOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <label className="flex items-center space-x-1.5 cursor-pointer text-xs font-medium text-zinc-700 dark:text-zinc-300 select-none">
            <input
              type="checkbox"
              checked={colorizeOnly}
              onChange={(e) => {
                const checked = e.target.checked;
                setColorizeOnly(checked);
                if (checked && colorizer === "none") setColorizer("mc2");
              }}
              className="rounded border-zinc-300 dark:border-zinc-600 text-purple-600 focus:ring-purple-500 h-3.5 w-3.5"
            />
            <span className={colorizeOnly ? "text-purple-600 dark:text-purple-400 font-semibold" : ""}>
              Colorize Only (Skip text translation)
            </span>
          </label>
        </div>
        {(colorizer !== "none" || colorizeOnly) && (
          <div className="flex w-full items-center justify-between gap-3 sm:w-auto sm:justify-normal">
            <div className="flex items-center space-x-1">
              <span className="text-xs text-zinc-500">Size:</span>
              <select
                value={colorizationSize}
                onChange={(e) => setColorizationSize(e.target.value)}
                className="rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-0.5 text-xs text-zinc-800 dark:text-zinc-200"
              >
                {colorizationSizes.map((s) => (
                  <option key={s} value={String(s)}>
                    {s}px
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center space-x-1">
              <span className="text-xs text-zinc-500">Tolerance:</span>
              <input
                type="number"
                step={1}
                value={colorThreshold}
                onChange={(e) => setColorThreshold(Number(e.target.value))}
                className="w-14 rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-0.5 text-xs text-zinc-800 dark:text-zinc-200"
                title="Color tolerance to skip colored pages (default 31, -1 to disable)"
              />
            </div>

          </div>
        )}
      </div>

      {/* Collapsible Advanced Engine Settings Toggle */}
      <div className="pt-1 border-t border-zinc-100 dark:border-zinc-800">
        <button
          type="button"
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="flex max-w-full flex-wrap items-center gap-x-2 gap-y-1 py-1 text-left text-xs font-semibold text-zinc-600 transition-colors hover:text-indigo-600 focus-visible:outline-none dark:text-zinc-400 dark:hover:text-indigo-400"
        >
          <Icon
            icon={showAdvanced ? "carbon:chevron-down" : "carbon:chevron-right"}
            className="h-4 w-4"
          />
          <span>Advanced Neural Engine Parameters</span>
          <span className="max-w-full truncate rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
            {textDetector} · {detectionResolution}px · {inpainter} · {colorizer !== "none" ? colorizer : "B&W"}
          </span>
        </button>

        {showAdvanced && (
          <div className="mt-4 pt-4 border-t border-dashed border-zinc-200 dark:border-zinc-800 space-y-5 animate-in fade-in duration-150">
            <div>
              <h4 className="mb-3 flex items-center space-x-1.5 text-xs font-bold uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
                <Icon icon="carbon:api" className="h-3.5 w-3.5" />
                <span>AI Translation</span>
              </h4>
              <label className="flex max-w-sm flex-col gap-1.5 text-xs font-semibold uppercase tracking-wider text-zinc-600 dark:text-zinc-300">
                AI request batch size
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={translationBatchSize}
                  disabled={!(["deepseek", "gemini", "openai", "groq", "openrouter", "custom_openai"] as TranslatorKey[]).includes(translator)}
                  onChange={(event) => setTranslationBatchSize(Math.min(100, Math.max(1, Number(event.target.value) || 1)))}
                  className="rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm font-normal text-zinc-900 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-800/90 dark:text-zinc-100"
                />
                <span className="normal-case font-normal text-zinc-500">Maximum pages combined into one GPT-style translation request.</span>
              </label>
            </div>
            {/* Detection & OCR Group */}
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wider text-zinc-400 dark:text-zinc-500 mb-3 flex items-center space-x-1.5">
                <Icon icon="carbon:search-locate" className="h-3.5 w-3.5" />
                <span>Text Detection & OCR</span>
              </h4>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <LabeledSelect
                  id="textDetector"
                  label="Text Detector"
                  icon="carbon:scan"
                  title="Detector model"
                  value={textDetector}
                  onChange={setTextDetector}
                  options={textDetectorOptions}
                  tooltip="CTD is optimized for stylized comic manga fonts. Paddle is great for webtoons."
                />
                <LabeledSelect
                  id="ocr"
                  label="OCR Model"
                  icon="carbon:character-patterns"
                  title="OCR model"
                  value={ocr}
                  onChange={setOcr}
                  options={ocrOptions}
                  tooltip="Model used to read the detected text. 48px is the default choice for manga."
                />
                <LabeledSelect
                  id="detectionResolution"
                  label="Detection Size"
                  icon="carbon:fit-to-screen"
                  title="Detection size"
                  value={detectionResolution}
                  onChange={setDetectionResolution}
                  options={detectionResolutions.map((res) => ({
                    label: `${res}px`,
                    value: String(res),
                  }))}
                  tooltip="Input resolution for the OCR detector. Higher values improve small text recognition."
                />
                <LabeledInput
                  id="boxThreshold"
                  label="Box Threshold"
                  icon="carbon:chart-bubble"
                  title="Confidence threshold for speech bubble detection"
                  step={0.01}
                  value={customBoxThreshold}
                  onChange={setCustomBoxThreshold}
                  tooltip="Confidence threshold (0.0–1.0) for detecting text boxes. Lower catches faint text."
                />
                <LabeledInput
                  id="unclipRatio"
                  label="Unclip Ratio"
                  icon="carbon:maximize"
                  title="Margin around text bounding boxes"
                  step={0.05}
                  value={customUnclipRatio}
                  onChange={setCustomUnclipRatio}
                  tooltip="Expansion ratio around detected text lines. Prevents cutting off font descenders."
                />
                <LabeledInput
                  id="ocrMinConfidence"
                  label="OCR Min Confidence"
                  icon="carbon:meter-alt"
                  title="Confidence threshold for OCR character recognition"
                  step={0.05}
                  min={0}
                  max={1}
                  placeholder="Default"
                  value={customOcrProb !== undefined ? customOcrProb : ""}
                  onChange={(val) => setCustomOcrProb(val > 0 ? val : undefined)}
                  tooltip="Minimum confidence threshold (0.0–1.0) for OCR text recognition. Drops regions whose OCR confidence is below this score. Leave empty for model default."
                />
              </div>
            </div>

            {/* Inpainting & Mask Group */}
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wider text-zinc-400 dark:text-zinc-500 mb-3 flex items-center space-x-1.5">
                <Icon icon="carbon:paint-brush" className="h-3.5 w-3.5" />
                <span>Inpainting & Mask Generation</span>
              </h4>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <LabeledSelect
                  id="inpainter"
                  label="Inpainter Model"
                  icon="carbon:color-palette"
                  title="Inpainter neural model"
                  value={inpainter}
                  onChange={setInpainter}
                  options={inpainterOptions}
                  tooltip="Lama Large produces clean background reconstruction behind removed text."
                />
                <LabeledSelect
                  id="inpaintingSize"
                  label="Inpainting Resolution"
                  icon="carbon:fit-to-width"
                  title="Inpainting processing size"
                  value={inpaintingSize}
                  onChange={setInpaintingSize}
                  options={inpaintingSizes.map((size) => ({
                    label: `${size}px`,
                    value: String(size),
                  }))}
                  tooltip="Resolution at which inpainter neural network fills background textures."
                />
                <LabeledInput
                  id="maskDilationOffset"
                  label="Mask Dilation"
                  icon="carbon:center-circle"
                  title="Pixel expansion of text mask"
                  step={1}
                  value={maskDilationOffset}
                  onChange={setMaskDilationOffset}
                  tooltip="Pixel padding expanding around characters to erase original text edges completely."
                />
                <label className="flex items-center gap-2 self-end pb-2 text-xs text-zinc-600 dark:text-zinc-300">
                  <input type="checkbox" checked={bubbleDetection} onChange={(event) => setBubbleDetection(event.target.checked)} />
                  Fit text to speech bubbles
                </label>
              </div>
            </div>

            {/* Colorization Group */}
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wider text-zinc-400 dark:text-zinc-500 mb-3 flex items-center space-x-1.5">
                <Icon icon="carbon:color-palette" className="h-3.5 w-3.5" />
                <span>Manga Colorization (B&W to Color)</span>
              </h4>
              <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
                <LabeledSelect
                  id="colorizerModelAdv"
                  label="Colorizer Model"
                  icon="carbon:color-palette"
                  title="Manga colorizer neural model"
                  value={colorizer}
                  onChange={(val) => {
                    setColorizer(val);
                    if (val === "none") setColorizeOnly(false);
                  }}
                  options={colorizerOptions}
                  tooltip="MC2 colorizes black and white manga pages with vibrant colors while preserving sharp original lineart."
                />
                <LabeledSelect
                  id="colorizationResolutionAdv"
                  label="Colorization Size"
                  icon="carbon:fit-to-screen"
                  title="Colorizer input resolution"
                  value={colorizationSize}
                  onChange={setColorizationSize}
                  options={colorizationSizes.map((size) => ({
                    label: `${size}px`,
                    value: String(size),
                  }))}
                  tooltip="Internal processing size for coloring. Default 576px offers optimal balance of speed and color nuance."
                />
                <LabeledInput
                  id="denoiseSigmaAdv"
                  label="Denoise Sigma"
                  icon="carbon:waveform"
                  title="Denoising filter strength"
                  step={1}
                  value={denoiseSigma}
                  onChange={setDenoiseSigma}
                  tooltip="Strength of the denoising filter applied during colorization (default 25, 0–255). Higher values remove more noise but can soften details. -1 to disable."
                />
                <LabeledInput
                  id="colorToleranceAdv"
                  label="Color Tolerance"
                  icon="carbon:meter-alt"
                  title="Skip already colored pages tolerance"
                  step={1}
                  value={colorThreshold}
                  onChange={setColorThreshold}
                  tooltip="CIELAB color distance tolerance. Pages with color distance > tolerance are skipped so already-colored pages aren't recolored (default 31). Set to -1 to disable."
                />
              </div>
            </div>

            {/* Upscaling Group */}
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wider text-zinc-400 dark:text-zinc-500 mb-3 flex items-center space-x-1.5">
                <Icon icon="carbon:zoom-in" className="h-3.5 w-3.5" />
                <span>Upscaling & Output Size</span>
              </h4>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <LabeledSelect
                  id="upscaler"
                  label="Upscaler Model"
                  icon="carbon:ai-status"
                  title="Upscaler model"
                  value={upscaler}
                  onChange={setUpscaler}
                  options={upscalerOptions}
                  tooltip="Model used to enlarge the image before processing."
                />
                <LabeledSelect
                  id="upscaleRatio"
                  label="Upscale Ratio"
                  icon="carbon:zoom-in-area"
                  title="Upscale ratio"
                  value={upscaleRatio}
                  onChange={(val) => {
                    setUpscaleRatio(val);
                    if (!val) {
                      setRevertUpscaling(false);
                    }
                  }}
                  options={upscaleRatioOptions}
                  tooltip="Choose Disabled to process at the original image size."
                />
                <label className={`flex items-center gap-2 self-end pb-2 text-xs font-medium ${upscaleRatio ? "text-zinc-700 dark:text-zinc-300 cursor-pointer" : "text-zinc-400 dark:text-zinc-500 opacity-60 cursor-not-allowed"}`}>
                  <input
                    type="checkbox"
                    checked={Boolean(upscaleRatio && revertUpscaling)}
                    disabled={!upscaleRatio}
                    onChange={(e) => setRevertUpscaling(e.target.checked)}
                    className="h-3.5 w-3.5 rounded border-zinc-300 text-indigo-600 focus:ring-indigo-500 dark:border-zinc-600 disabled:opacity-50"
                  />
                  <span title="Downscale the final result back to the original image dimensions">
                    Return final image to original size
                  </span>
                </label>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
