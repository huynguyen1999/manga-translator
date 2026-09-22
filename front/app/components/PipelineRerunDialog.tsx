import React, { useState } from "react";
import { Icon } from "@iconify/react";
import { fontOptions, ocrOptions } from "@/config";
import type { FinishedImage, PipelineRerunMode, TranslationSettings } from "@/types";
import { validTranslators } from "@/types";
import { getTranslatorName } from "@/utils/getTranslatorName";

interface PipelineRerunDialogProps {
  images: FinishedImage[];
  groupId?: string;
  mangaTitle?: string;
  onClose: () => void;
  onSubmit: (options: {
    mode: PipelineRerunMode;
    settingsOverrides?: Partial<TranslationSettings>;
  }) => Promise<void> | void;
}

interface PresetOption {
  id: PipelineRerunMode;
  title: string;
  badge?: string;
  description: string;
  summarySteps: string;
  details: Array<{
    stage: string;
    action: "rerun" | "reuse" | "remap";
    note?: string;
  }>;
}

const PRESETS: PresetOption[] = [
  {
    id: "reprocess_text",
    title: "Re-detect text + preserve translations",
    badge: "Recommended for fixing OCR / inpainting",
    description: "Improve text detection, OCR, and inpainting while keeping your existing translations.",
    summarySteps: "Detection → OCR → Inpaint → Remap → Typeset",
    details: [
      { stage: "Detection", action: "rerun", note: "Re-detects all text lines with chosen detector settings" },
      { stage: "OCR", action: "rerun", note: "Extracts Japanese text using selected OCR model" },
      { stage: "Speech Bubbles", action: "reuse", note: "Reuses existing bubble shapes when available" },
      { stage: "Mask & Inpaint", action: "rerun", note: "Regenerates tight masks and cleans speech bubbles" },
      { stage: "Translation", action: "remap", note: "Remaps previous translations using geometry & bubble affinity" },
      { stage: "Typesetting", action: "rerun", note: "Lays out dialogue with shape-aware typography solver" },
    ],
  },
  {
    id: "typesetting",
    title: "Re-typeset only (Fast)",
    badge: "Instant layout refresh",
    description: "Rerender text layout with updated font, lettering case, alignment, or renderer settings. Skips OCR and inpainting.",
    summarySteps: "Reuse inpainting & translations → Shape-aware layout & render",
    details: [
      { stage: "Detection & OCR", action: "reuse", note: "Reuses existing text regions and boundaries" },
      { stage: "Inpainting", action: "reuse", note: "Keeps existing clean background canvas" },
      { stage: "Translation", action: "reuse", note: "Preserves existing translations" },
      { stage: "Typesetting", action: "rerun", note: "Re-runs typography solver with updated font and styling" },
    ],
  },
  {
    id: "translation_typesetting",
    title: "Retranslate + typeset",
    description: "Retranslate using a different AI model or target language, then typeset onto the existing inpainted canvas.",
    summarySteps: "Reuse OCR & inpainting → Retranslate → Typeset",
    details: [
      { stage: "Detection & OCR", action: "reuse", note: "Uses current OCR textlines" },
      { stage: "Inpainting", action: "reuse", note: "Keeps existing clean background" },
      { stage: "Translation", action: "rerun", note: "Queries selected translator model" },
      { stage: "Typesetting", action: "rerun", note: "Rerenders translated text into bubbles" },
    ],
  },
  {
    id: "full",
    title: "Full pipeline rerun",
    description: "Rerun everything from the original raw image (detection, OCR, inpainting, translation, and typesetting).",
    summarySteps: "Raw input → Full translation pipeline",
    details: [
      { stage: "Detection", action: "rerun" },
      { stage: "OCR", action: "rerun" },
      { stage: "Speech Bubbles", action: "rerun" },
      { stage: "Inpainting", action: "rerun" },
      { stage: "Translation", action: "rerun" },
      { stage: "Typesetting", action: "rerun" },
    ],
  },
];

export const PipelineRerunDialog: React.FC<PipelineRerunDialogProps> = ({
  images,
  groupId,
  mangaTitle,
  onClose,
  onSubmit,
}) => {
  const [selectedMode, setSelectedMode] = useState<PipelineRerunMode>("reprocess_text");
  const [expandedDetails, setExpandedDetails] = useState<Record<string, boolean>>({
    reprocess_text: false,
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Settings overrides
  const [selectedTranslator, setSelectedTranslator] = useState<string>("gemini");
  const [selectedOcr, setSelectedOcr] = useState<string>("48px_ctc");
  const [customOcrProb, setCustomOcrProb] = useState<number>(0.3);
  const [renderFont, setRenderFont] = useState<string>("wildwords");
  const [renderAlignment, setRenderAlignment] = useState<string>("center");

  const toggleDetails = (modeId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setExpandedDetails((prev) => ({ ...prev, [modeId]: !prev[modeId] }));
  };

  const handleExecute = async () => {
    setError(null);
    setIsSubmitting(true);
    try {
      const overrides: Partial<TranslationSettings> = {};
      if (renderFont) {
        overrides.renderFont = renderFont;
      }
      if (selectedMode === "translation_typesetting") {
        overrides.translator = selectedTranslator as any;
      } else if (selectedMode === "reprocess_text") {
        overrides.ocr = selectedOcr;
        overrides.customOcrProb = customOcrProb;
      } else if (selectedMode === "typesetting") {
        if (renderAlignment) overrides.renderAlignment = renderAlignment;
      }

      await onSubmit({
        mode: selectedMode,
        settingsOverrides: Object.keys(overrides).length > 0 ? overrides : undefined,
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to queue pipeline rerun.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const pageCount = images.length;
  const targetLabel = pageCount === 1 ? images[0].originalName : `${pageCount} selected pages`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-[2px] isolate" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="rerun-dialog-title"
        className="w-full max-w-2xl overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-900 transform-gpu"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
          <div>
            <h2 id="rerun-dialog-title" className="text-lg font-bold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
              <Icon icon="carbon:reset" className="h-5 w-5 text-indigo-600 dark:text-indigo-400" />
              Rerun Pipeline
            </h2>
            <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
              {targetLabel} {mangaTitle ? `· ${mangaTitle}` : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
            aria-label="Close"
          >
            <Icon icon="carbon:close" className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="max-h-[75vh] overflow-y-auto px-6 py-4 space-y-3">
          {error && (
            <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300">
              {error}
            </div>
          )}

          <div className="space-y-2.5">
            {PRESETS.map((preset) => {
              const isSelected = selectedMode === preset.id;
              const isExpanded = Boolean(expandedDetails[preset.id]);

              return (
                <div
                  key={preset.id}
                  onClick={() => setSelectedMode(preset.id)}
                  className={`rounded-xl border p-4 cursor-pointer transition-all ${
                    isSelected
                      ? "border-indigo-600 bg-indigo-50/40 ring-2 ring-indigo-500/20 dark:border-indigo-500 dark:bg-indigo-950/20 dark:ring-indigo-500/30"
                      : "border-zinc-200 hover:border-zinc-300 bg-zinc-50/50 dark:border-zinc-800 dark:hover:border-zinc-700 dark:bg-zinc-900/50"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-start gap-3 min-w-0">
                      <div className="pt-0.5">
                        <input
                          type="radio"
                          name="rerunMode"
                          checked={isSelected}
                          onChange={() => setSelectedMode(preset.id)}
                          className="h-4 w-4 text-indigo-600 focus:ring-indigo-500"
                        />
                      </div>
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                            {preset.title}
                          </span>
                          {preset.badge && (
                            <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300">
                              {preset.badge}
                            </span>
                          )}
                        </div>
                        <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">
                          {preset.description}
                        </p>
                        <p className="mt-1 font-mono text-[11px] text-indigo-600 dark:text-indigo-400">
                          {preset.summarySteps}
                        </p>
                      </div>
                    </div>

                    <button
                      type="button"
                      onClick={(e) => toggleDetails(preset.id, e)}
                      className="shrink-0 text-xs text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200 inline-flex items-center gap-1"
                    >
                      <span>{isExpanded ? "Hide details" : "Show details"}</span>
                      <Icon
                        icon={isExpanded ? "carbon:chevron-up" : "carbon:chevron-down"}
                        className="h-3 w-3"
                      />
                    </button>
                  </div>

                  {isExpanded && (
                    <div className="mt-3 border-t border-zinc-200 pt-3 dark:border-zinc-800 pl-7">
                      <div className="space-y-1.5">
                        {preset.details.map((step, idx) => (
                          <div key={idx} className="flex items-center gap-2 text-xs">
                            <span
                              className={`inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold ${
                                step.action === "rerun"
                                  ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
                                  : step.action === "remap"
                                  ? "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                                  : "bg-zinc-200 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
                              }`}
                            >
                              {step.action === "rerun" ? "●" : step.action === "remap" ? "↔" : "✓"}
                            </span>
                            <span className="font-medium text-zinc-800 dark:text-zinc-200">
                              {step.stage}
                            </span>
                            <span className="text-zinc-400 dark:text-zinc-500">
                              ({step.action}{step.note ? ` · ${step.note}` : ""})
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Mode-specific settings controls */}
                  {isSelected && preset.id === "reprocess_text" && (
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
                  )}

                  {isSelected && preset.id === "typesetting" && (
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
                  )}

                  {isSelected && preset.id === "translation_typesetting" && (
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
                  )}

                  {isSelected && preset.id === "full" && (
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
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2.5 border-t border-zinc-200 bg-zinc-50/50 px-6 py-3.5 dark:border-zinc-800 dark:bg-zinc-950/50">
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            className="rounded-lg border border-zinc-200 px-4 py-2 text-xs font-semibold text-zinc-700 hover:bg-white dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleExecute}
            disabled={isSubmitting}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-70"
          >
            <Icon icon="carbon:play" className="h-3.5 w-3.5" />
            <span>{isSubmitting ? "Queueing…" : `Rerun ${pageCount === 1 ? "1 page" : `${pageCount} pages`}`}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
