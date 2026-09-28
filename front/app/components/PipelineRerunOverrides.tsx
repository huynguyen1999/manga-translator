import React from "react";
import { fontOptions, ocrOptions } from "@/config";
import { validTranslators, type PipelineRerunMode } from "@/types";
import { getTranslatorName } from "@/utils/getTranslatorName";

export interface PipelineRerunOverridesProps {
  mode: PipelineRerunMode;
  selectedOcr: string;
  setSelectedOcr: (val: string) => void;
  customOcrProb: number;
  setCustomOcrProb: (val: number) => void;
  renderFont: string;
  setRenderFont: (val: string) => void;
  renderAlignment: string;
  setRenderAlignment: (val: string) => void;
  selectedTranslator: string;
  setSelectedTranslator: (val: string) => void;
}

export const PipelineRerunOverrides: React.FC<PipelineRerunOverridesProps> = ({
  mode,
  selectedOcr,
  setSelectedOcr,
  customOcrProb,
  setCustomOcrProb,
  renderFont,
  setRenderFont,
  renderAlignment,
  setRenderAlignment,
  selectedTranslator,
  setSelectedTranslator,
}) => {
  if (mode === "reprocess_text") {
    return (
      <div className="mt-3 border-t border-indigo-200/60 pt-3 dark:border-indigo-900/60 pl-7 space-y-2" onClick={(e) => e.stopPropagation()}>
        <span className="block text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
          OCR & Typography Overrides
        </span>
        <div className="flex flex-wrap items-center gap-3">
          <label className="inline-flex items-center gap-1.5 text-xs text-zinc-700 dark:text-zinc-300">
            <span>OCR Model:</span>
            <select
              value={selectedOcr}
              onChange={(e) => setSelectedOcr(e.target.value)}
              className="rounded-lg border border-zinc-200 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-800"
            >
              {ocrOptions.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="inline-flex items-center gap-1.5 text-xs text-zinc-700 dark:text-zinc-300">
            <span>Min Confidence:</span>
            <input
              type="number"
              min="0"
              max="1"
              step="0.05"
              value={customOcrProb}
              onChange={(e) => setCustomOcrProb(parseFloat(e.target.value) || 0)}
              className="w-16 rounded-lg border border-zinc-200 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-800"
            />
          </label>
          <label className="inline-flex items-center gap-1.5 text-xs text-zinc-700 dark:text-zinc-300">
            <span>Font:</span>
            <select
              value={renderFont}
              onChange={(e) => setRenderFont(e.target.value)}
              className="rounded-lg border border-zinc-200 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-800"
            >
              {fontOptions.map((font) => (
                <option key={font.value} value={font.value}>{font.label}</option>
              ))}
            </select>
          </label>
        </div>
      </div>
    );
  }

  if (mode === "typesetting") {
    return (
      <div className="mt-3 border-t border-indigo-200/60 pt-3 dark:border-indigo-900/60 pl-7 space-y-2" onClick={(e) => e.stopPropagation()}>
        <span className="block text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
          Typography & Lettering Overrides
        </span>
        <div className="flex flex-wrap items-center gap-3">
          <label className="inline-flex items-center gap-1.5 text-xs text-zinc-700 dark:text-zinc-300">
            <span>Font:</span>
            <select
              value={renderFont}
              onChange={(e) => setRenderFont(e.target.value)}
              className="rounded-lg border border-zinc-200 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-800"
            >
              {fontOptions.map((font) => (
                <option key={font.value} value={font.value}>{font.label}</option>
              ))}
            </select>
          </label>
          <label className="inline-flex items-center gap-1.5 text-xs text-zinc-700 dark:text-zinc-300">
            <span>Alignment:</span>
            <select
              value={renderAlignment}
              onChange={(e) => setRenderAlignment(e.target.value)}
              className="rounded-lg border border-zinc-200 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-800"
            >
              <option value="center">Center</option>
              <option value="left">Left</option>
              <option value="right">Right</option>
              <option value="auto">Auto</option>
            </select>
          </label>
        </div>
      </div>
    );
  }

  if (mode === "translation_typesetting") {
    return (
      <div className="mt-3 border-t border-indigo-200/60 pt-3 dark:border-indigo-900/60 pl-7 space-y-2" onClick={(e) => e.stopPropagation()}>
        <span className="block text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
          Translation & Lettering Service
        </span>
        <div className="flex flex-wrap items-center gap-3">
          <label className="inline-flex items-center gap-1.5 text-xs text-zinc-700 dark:text-zinc-300">
            <span>Translator:</span>
            <select
              value={selectedTranslator}
              onChange={(e) => setSelectedTranslator(e.target.value)}
              className="rounded-lg border border-zinc-200 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-800"
            >
              {validTranslators.map((t) => (
                <option key={t} value={t}>
                  {getTranslatorName(t)}
                </option>
              ))}
            </select>
          </label>
          <label className="inline-flex items-center gap-1.5 text-xs text-zinc-700 dark:text-zinc-300">
            <span>Font:</span>
            <select
              value={renderFont}
              onChange={(e) => setRenderFont(e.target.value)}
              className="rounded-lg border border-zinc-200 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-800"
            >
              {fontOptions.map((font) => (
                <option key={font.value} value={font.value}>{font.label}</option>
              ))}
            </select>
          </label>
        </div>
      </div>
    );
  }

  if (mode === "full") {
    return (
      <div className="mt-3 border-t border-indigo-200/60 pt-3 dark:border-indigo-900/60 pl-7 space-y-2" onClick={(e) => e.stopPropagation()}>
        <span className="block text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
          Typography Overrides
        </span>
        <div className="flex flex-wrap items-center gap-3">
          <label className="inline-flex items-center gap-1.5 text-xs text-zinc-700 dark:text-zinc-300">
            <span>Font:</span>
            <select
              value={renderFont}
              onChange={(e) => setRenderFont(e.target.value)}
              className="rounded-lg border border-zinc-200 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-800"
            >
              {fontOptions.map((font) => (
                <option key={font.value} value={font.value}>{font.label}</option>
              ))}
            </select>
          </label>
        </div>
      </div>
    );
  }

  return null;
};
