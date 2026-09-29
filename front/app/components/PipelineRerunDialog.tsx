import React, { useState } from "react";
import { Icon } from "@iconify/react";
import type { FinishedImage, PipelineRerunMode, TranslationSettings } from "@/types";
import { AppOverlayPortal } from "@/components/AppOverlayPortal";
import { PRESETS } from "./pipelineRerunPresets";
import { PipelineRerunOverrides } from "./PipelineRerunOverrides";
import { useModalEscape } from "@/utils/useModalEscape";

export type RerunSettingsSource = "app" | "snapshot";

export interface PipelineRerunDialogProps {
  images: FinishedImage[];
  mangaTitle?: string;
  appSettings?: Partial<TranslationSettings>;
  onClose: () => void;
  onSubmit: (options: {
    mode: PipelineRerunMode;
    settingsOverrides?: Partial<TranslationSettings>;
  }) => Promise<void> | void;
}

export const PipelineRerunDialog: React.FC<PipelineRerunDialogProps> = ({
  images,
  mangaTitle,
  appSettings,
  onClose,
  onSubmit,
}) => {
  const [selectedMode, setSelectedMode] = useState<PipelineRerunMode>("reprocess_text");
  const [settingsSource, setSettingsSource] = useState<RerunSettingsSource>("app");
  const [expandedDetails, setExpandedDetails] = useState<Record<string, boolean>>({
    reprocess_text: false,
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Settings overrides initialized from appSettings if present
  const [selectedTranslator, setSelectedTranslator] = useState<string>(
    appSettings?.translator || "gemini"
  );
  const [selectedOcr, setSelectedOcr] = useState<string>(
    appSettings?.ocr || "48px_ctc"
  );
  const [customOcrProb, setCustomOcrProb] = useState<number>(
    appSettings?.customOcrProb ?? 0.3
  );
  const [renderFont, setRenderFont] = useState<string>(
    appSettings?.renderFont || "wildwords"
  );
  const [renderAlignment, setRenderAlignment] = useState<string>(
    (appSettings as Record<string, any>)?.renderAlignment || "center"
  );

  const toggleDetails = (modeId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setExpandedDetails((prev) => ({ ...prev, [modeId]: !prev[modeId] }));
  };

  const handleExecute = async () => {
    setError(null);
    setIsSubmitting(true);
    try {
      let overrides: Partial<TranslationSettings> | undefined = undefined;

      if (settingsSource === "app") {
        overrides = {
          ...(appSettings || {}),
        };
        if (renderFont) overrides.renderFont = renderFont;
        if (selectedMode === "translation_typesetting") {
          overrides.translator = selectedTranslator as any;
        } else if (selectedMode === "reprocess_text") {
          overrides.ocr = selectedOcr;
          overrides.customOcrProb = customOcrProb;
        } else if (selectedMode === "typesetting") {
          if (renderAlignment) overrides.renderAlignment = renderAlignment;
        }
      } else {
        const manualOverrides: Partial<TranslationSettings> = {};
        if (renderFont && renderFont !== (appSettings?.renderFont || "wildwords")) {
          manualOverrides.renderFont = renderFont;
        }
        if (
          selectedMode === "translation_typesetting" &&
          selectedTranslator !== (appSettings?.translator || "gemini")
        ) {
          manualOverrides.translator = selectedTranslator as any;
        } else if (selectedMode === "reprocess_text") {
          if (selectedOcr !== (appSettings?.ocr || "48px_ctc")) {
            manualOverrides.ocr = selectedOcr;
          }
          if (customOcrProb !== (appSettings?.customOcrProb ?? 0.3)) {
            manualOverrides.customOcrProb = customOcrProb;
          }
        } else if (selectedMode === "typesetting" && renderAlignment !== "center") {
          manualOverrides.renderAlignment = renderAlignment;
        }

        if (Object.keys(manualOverrides).length > 0) {
          overrides = manualOverrides;
        }
      }

      await onSubmit({
        mode: selectedMode,
        settingsOverrides: overrides,
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
  useModalEscape(true, onClose);

  return (
    <AppOverlayPortal>
      <div
        data-app-overlay="pipeline-rerun"
        className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-[2px] isolate"
        onClick={onClose}
      >
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
          <div className="max-h-[75vh] overflow-y-auto px-6 py-4 space-y-4">
            {error && (
              <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300">
                {error}
              </div>
            )}

            {/* Settings Source Selection */}
            <div className="rounded-xl border border-zinc-200 bg-zinc-50/70 p-3.5 dark:border-zinc-800 dark:bg-zinc-900/50 space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-1.5">
                  <Icon icon="carbon:settings-adjust" className="h-4 w-4 text-indigo-600 dark:text-indigo-400" />
                  Pipeline Settings Source
                </span>
                <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                  Choose parameters for this rerun
                </span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <label
                  className={`flex cursor-pointer items-start gap-2.5 rounded-lg border p-2.5 transition-all ${
                    settingsSource === "app"
                      ? "border-indigo-600 bg-white shadow-sm ring-1 ring-indigo-500/20 dark:border-indigo-500 dark:bg-zinc-800 dark:ring-indigo-500/30"
                      : "border-zinc-200 bg-transparent hover:border-zinc-300 hover:bg-white/50 dark:border-zinc-750 dark:hover:border-zinc-700 dark:hover:bg-zinc-800/50"
                  }`}
                >
                  <input
                    type="radio"
                    name="settingsSource"
                    value="app"
                    checked={settingsSource === "app"}
                    onChange={() => setSettingsSource("app")}
                    className="mt-0.5 h-3.5 w-3.5 text-indigo-600 focus:ring-indigo-500"
                  />
                  <div className="min-w-0">
                    <span className="block text-xs font-medium text-zinc-900 dark:text-zinc-100">
                      Current App Settings
                    </span>
                    <span className="mt-0.5 block text-[11px] leading-tight text-zinc-500 dark:text-zinc-400">
                      Use active Studio options (translator, OCR, font, inpainting, upscaler)
                    </span>
                  </div>
                </label>

                <label
                  className={`flex cursor-pointer items-start gap-2.5 rounded-lg border p-2.5 transition-all ${
                    settingsSource === "snapshot"
                      ? "border-indigo-600 bg-white shadow-sm ring-1 ring-indigo-500/20 dark:border-indigo-500 dark:bg-zinc-800 dark:ring-indigo-500/30"
                      : "border-zinc-200 bg-transparent hover:border-zinc-300 hover:bg-white/50 dark:border-zinc-750 dark:hover:border-zinc-700 dark:hover:bg-zinc-800/50"
                  }`}
                >
                  <input
                    type="radio"
                    name="settingsSource"
                    value="snapshot"
                    checked={settingsSource === "snapshot"}
                    onChange={() => setSettingsSource("snapshot")}
                    className="mt-0.5 h-3.5 w-3.5 text-indigo-600 focus:ring-indigo-500"
                  />
                  <div className="min-w-0">
                    <span className="block text-xs font-medium text-zinc-900 dark:text-zinc-100">
                      Page Snapshot Settings
                    </span>
                    <span className="mt-0.5 block text-[11px] leading-tight text-zinc-500 dark:text-zinc-400">
                      Preserve original settings saved when this page was translated
                    </span>
                  </div>
                </label>
              </div>
            </div>

            {/* Presets */}
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
                    {isSelected && (
                      <PipelineRerunOverrides
                        mode={preset.id}
                        selectedOcr={selectedOcr}
                        setSelectedOcr={setSelectedOcr}
                        customOcrProb={customOcrProb}
                        setCustomOcrProb={setCustomOcrProb}
                        renderFont={renderFont}
                        setRenderFont={setRenderFont}
                        renderAlignment={renderAlignment}
                        setRenderAlignment={setRenderAlignment}
                        selectedTranslator={selectedTranslator}
                        setSelectedTranslator={setSelectedTranslator}
                      />
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
    </AppOverlayPortal>
  );
};
