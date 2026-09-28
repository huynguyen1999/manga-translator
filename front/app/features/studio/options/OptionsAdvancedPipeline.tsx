import React from "react";
import { Icon } from "@iconify/react";
import {
  detectionResolutions,
  ocrOptions,
  textDetectorOptions,
  inpaintingSizes,
  inpainterOptions,
  summaryModelOptions,
} from "@/config";
import { LabeledInput } from "@/components/LabeledInput";
import { LabeledSelect } from "@/components/LabeledSelect";

interface OptionsAdvancedPipelineProps {
  showAdvanced: boolean;
  setShowAdvanced: (val: boolean) => void;
  detectionResolution: string;
  setDetectionResolution: (val: string) => void;
  textDetector: string;
  setTextDetector: (val: string) => void;
  ocr: string;
  setOcr: (val: string) => void;
  customBoxThreshold: number;
  setCustomBoxThreshold: (val: number) => void;
  customUnclipRatio: number;
  setCustomUnclipRatio: (val: number) => void;
  customOcrProb?: number;
  setCustomOcrProb: (val: number | undefined) => void;
  inpainter: string;
  setInpainter: (val: string) => void;
  inpaintingSize: string;
  setInpaintingSize: (val: string) => void;
  maskDilationOffset: number;
  setMaskDilationOffset: (val: number) => void;
  bubbleDetection: boolean;
  setBubbleDetection: (val: boolean) => void;
  bubbleConfidence: number;
  setBubbleConfidence: (val: number) => void;
  summaryModel: string;
  setSummaryModel: (val: string) => void;
}

export const OptionsAdvancedPipeline: React.FC<OptionsAdvancedPipelineProps> = ({
  showAdvanced,
  setShowAdvanced,
  detectionResolution,
  setDetectionResolution,
  textDetector,
  setTextDetector,
  ocr,
  setOcr,
  customBoxThreshold,
  setCustomBoxThreshold,
  customUnclipRatio,
  setCustomUnclipRatio,
  customOcrProb,
  setCustomOcrProb,
  inpainter,
  setInpainter,
  inpaintingSize,
  setInpaintingSize,
  maskDilationOffset,
  setMaskDilationOffset,
  bubbleDetection,
  setBubbleDetection,
  bubbleConfidence,
  setBubbleConfidence,
  summaryModel,
  setSummaryModel,
}) => {
  return (
    <div className="border-t border-zinc-100 pt-1 dark:border-zinc-800/80">
      <button
        type="button"
        onClick={() => setShowAdvanced(!showAdvanced)}
        className="flex w-full items-center justify-between rounded-lg py-1.5 text-left text-xs font-semibold text-zinc-600 transition-colors hover:text-indigo-600 focus-visible:outline-none dark:text-zinc-400 dark:hover:text-indigo-400"
      >
        <div className="flex items-center space-x-2">
          <Icon
            icon={showAdvanced ? "carbon:chevron-down" : "carbon:chevron-right"}
            className="h-4 w-4 text-zinc-400"
          />
          <span>Advanced Neural Engine Parameters</span>
        </div>
        <span className="truncate rounded-full bg-zinc-100 px-2.5 py-0.5 text-[11px] font-normal text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
          {textDetector} · {detectionResolution}px · {ocr} · {inpainter} · {bubbleDetection ? "Frames ON" : "Frames OFF"}
        </span>
      </button>

      {showAdvanced && (
        <div className="mt-3 space-y-3.5 border-t border-dashed border-zinc-200 pt-3 dark:border-zinc-800 animate-in fade-in duration-150">
          {/* Detection & OCR Sub-card */}
          <div className="rounded-lg border border-zinc-200/60 bg-zinc-50/40 p-3 dark:border-zinc-800/60 dark:bg-zinc-800/20 space-y-3">
            <div className="flex items-center space-x-1.5 text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
              <Icon icon="carbon:search-locate" className="h-3.5 w-3.5" />
              <span>Text Detection & OCR</span>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
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
                tooltip="Model used to read detected text. 48px is the default choice for manga."
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
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <LabeledInput
                id="boxThreshold"
                label="Box Threshold"
                icon="carbon:chart-bubble"
                title="Confidence threshold for text detection"
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
                tooltip="Minimum confidence threshold (0.0–1.0) for OCR text recognition. Drops regions whose confidence is below this score."
              />
            </div>
          </div>

          {/* Inpainting & Frame Detection Sub-card */}
          <div className="rounded-lg border border-zinc-200/60 bg-zinc-50/40 p-3 dark:border-zinc-800/60 dark:bg-zinc-800/20 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-1.5 text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                <Icon icon="carbon:paint-brush" className="h-3.5 w-3.5" />
                <span>Inpainting & Frame Detection</span>
              </div>
              <label className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-zinc-700 select-none dark:text-zinc-300" title="Detect panels and speech bubbles for frame-aware text fitting">
                <input
                  type="checkbox"
                  checked={bubbleDetection}
                  onChange={(e) => setBubbleDetection(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-zinc-300 text-indigo-600 focus:ring-indigo-500 dark:border-zinc-600"
                />
                <span>Frame detection</span>
              </label>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
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
                icon="carbon:fit-to-screen"
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
              <LabeledInput
                id="bubbleConfidence"
                label="Frame Confidence"
                icon="carbon:chart-bubble"
                title="Confidence threshold for speech bubble and frame detector"
                step={0.05}
                min={0.05}
                max={0.95}
                value={bubbleConfidence}
                onChange={setBubbleConfidence}
                tooltip="Minimum confidence threshold (0.05–0.95) for speech bubble and frame detection. Detections with confidence below this are discarded."
              />
            </div>
          </div>

          {/* Synopsis & Auxiliary Sub-card */}
          <div className="rounded-lg border border-zinc-200/60 bg-zinc-50/40 p-3 dark:border-zinc-800/60 dark:bg-zinc-800/20">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
              <div className="space-y-0.5">
                <div className="flex items-center space-x-1.5 text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                  <Icon icon="carbon:document-sentiment" className="h-3.5 w-3.5" />
                  <span>Manga Synopsis Model</span>
                </div>
                <p className="text-[11px] text-zinc-400">
                  Global model used for generating manga series summaries in the Gallery.
                </p>
              </div>
              <div className="w-full sm:w-64">
                <select
                  id="summaryModel"
                  value={summaryModel}
                  onChange={(e) => setSummaryModel(e.target.value)}
                  className="w-full rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-800 shadow-xs focus:border-indigo-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
                >
                  {summaryModelOptions.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
