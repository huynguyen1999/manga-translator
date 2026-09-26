import React from 'react';
import { Icon } from '@iconify/react';
import type { ResolvedPipelineStepSettings } from '@/utils/pageDetailSettings';

interface StepSettingsTabProps {
  stepSettings: ResolvedPipelineStepSettings;
  engine: string;
  model: string;
  isTranslationDetailLoading: boolean;
}

export const StepSettingsTab: React.FC<StepSettingsTabProps> = ({
  stepSettings,
  engine,
  model,
  isTranslationDetailLoading,
}) => (
<div className="space-y-3">
                          {/* Translation Details Card */}
                          <section className="rounded-lg border border-indigo-400/20 bg-indigo-500/10 p-3" aria-label="Translation details">
                            <div className="mb-2.5 flex items-center justify-between gap-2">
                              <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-indigo-200">
                                <Icon icon="carbon:machine-learning-model" className="h-4 w-4 text-indigo-300" />
                                Translation details
                              </h3>
                              {isTranslationDetailLoading && (
                                <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin text-indigo-300" aria-label="Loading model details" />
                              )}
                            </div>
                            <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
                              <div>
                                <dt className="text-zinc-400">Engine</dt>
                                <dd className="mt-0.5 truncate font-medium text-white" title={engine}>{engine}</dd>
                              </div>
                              <div>
                                <dt className="text-zinc-400">Model</dt>
                                <dd className="mt-0.5 break-words font-mono font-medium text-indigo-100" title={model}>{model}</dd>
                              </div>
                              {(stepSettings.translation.sourceLanguage || stepSettings.translation.targetLanguage) && (
                                <div className="col-span-2">
                                  <dt className="text-zinc-400">Languages</dt>
                                  <dd className="mt-0.5 font-medium text-zinc-200 truncate" title={`${stepSettings.translation.sourceLanguage || "Auto"} → ${stepSettings.translation.targetLanguage || "ENG"}`}>
                                    {stepSettings.translation.sourceLanguage || "Auto"} → {stepSettings.translation.targetLanguage || "ENG"}
                                  </dd>
                                </div>
                              )}
                            </dl>
                          </section>

                          {/* Detection & OCR Card */}
                          <div className="space-y-2 text-xs">
                            <div className="rounded-lg border border-white/10 bg-black/20 p-2.5">
                              <div className="mb-2 flex items-center gap-1.5 font-semibold text-zinc-200">
                                <Icon icon="carbon:search-locate" className="h-3.5 w-3.5 text-indigo-300" />
                                <span>Detection & OCR</span>
                              </div>
                              <dl className="grid grid-cols-2 gap-x-2 gap-y-1.5 text-[11px]">
                                <div>
                                  <dt className="text-zinc-400">Detector</dt>
                                  <dd className="font-medium text-white truncate" title={stepSettings.detection.detectorLabel}>{stepSettings.detection.detectorLabel}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Resolution</dt>
                                  <dd className="font-mono font-medium text-indigo-200">{stepSettings.detection.resolution || "—"}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Box threshold</dt>
                                  <dd className="font-mono text-zinc-200">{stepSettings.detection.boxThreshold ?? "—"}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Unclip ratio</dt>
                                  <dd className="font-mono text-zinc-200">{stepSettings.detection.unclipRatio ?? "—"}</dd>
                                </div>
                                <div className="col-span-2 border-t border-white/5 pt-1.5 mt-0.5">
                                  <dt className="text-zinc-400">OCR Model</dt>
                                  <dd className="font-medium text-white flex items-center justify-between gap-1">
                                    <span className="truncate" title={stepSettings.ocr.modelLabel}>{stepSettings.ocr.modelLabel}</span>
                                    <div className="flex items-center gap-1 shrink-0">
                                      {stepSettings.ocr.prob != null && (
                                        <span className="rounded bg-amber-500/20 px-1 text-[10px] text-amber-300">Min Conf: {stepSettings.ocr.prob}</span>
                                      )}
                                      {stepSettings.ocr.useMocrMerge && (
                                        <span className="rounded bg-indigo-500/20 px-1 text-[10px] text-indigo-300">Merge BBox</span>
                                      )}
                                    </div>
                                  </dd>
                                </div>
                              </dl>
                            </div>

                            {/* Speech Bubble Detection Card */}
                            <div className="rounded-lg border border-white/10 bg-black/20 p-2.5">
                              <div className="mb-1.5 flex items-center justify-between font-semibold text-zinc-200">
                                <span className="flex items-center gap-1.5">
                                  <Icon icon="carbon:chat" className="h-3.5 w-3.5 text-indigo-300" />
                                  <span>Speech Bubbles</span>
                                </span>
                                <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${stepSettings.bubbleDetection.enabled ? "bg-emerald-500/15 text-emerald-300" : "bg-zinc-500/20 text-zinc-400"}`}>
                                  {stepSettings.bubbleDetection.enabled ? "Enabled" : "Disabled"}
                                </span>
                              </div>
                              {stepSettings.bubbleDetection.enabled ? (
                                <dl className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
                                  <div>
                                    <dt className="text-zinc-400">Model</dt>
                                    <dd className="font-medium text-zinc-200">{stepSettings.bubbleDetection.model}</dd>
                                  </div>
                                  <div>
                                    <dt className="text-zinc-400">Confidence</dt>
                                    <dd className="font-mono text-zinc-200">{stepSettings.bubbleDetection.confidence ?? "—"}</dd>
                                  </div>
                                  <div>
                                    <dt className="text-zinc-400">Mask threshold</dt>
                                    <dd className="font-mono text-zinc-200">{stepSettings.bubbleDetection.maskThreshold ?? "—"}</dd>
                                  </div>
                                  {stepSettings.bubbleDetection.size ? (
                                    <div>
                                      <dt className="text-zinc-400">Input size</dt>
                                      <dd className="font-mono text-zinc-200">{stepSettings.bubbleDetection.size} px</dd>
                                    </div>
                                  ) : null}
                                </dl>
                              ) : (
                                <p className="text-[11px] text-zinc-400">Bubble shape segmentation turned off</p>
                              )}
                            </div>

                            {/* Inpainting & Mask Card */}
                            <div className="rounded-lg border border-white/10 bg-black/20 p-2.5">
                              <div className="mb-2 flex items-center gap-1.5 font-semibold text-zinc-200">
                                <Icon icon="carbon:erase" className="h-3.5 w-3.5 text-indigo-300" />
                                <span>Inpainting & Mask</span>
                              </div>
                              <dl className="grid grid-cols-2 gap-x-2 gap-y-1.5 text-[11px]">
                                <div>
                                  <dt className="text-zinc-400">Inpainter</dt>
                                  <dd className="font-medium text-white truncate" title={stepSettings.inpainting.inpainterLabel}>{stepSettings.inpainting.inpainterLabel}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Resolution</dt>
                                  <dd className="font-mono font-medium text-indigo-200">{stepSettings.inpainting.size || "—"}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Mask dilation</dt>
                                  <dd className="font-mono text-zinc-200">{stepSettings.inpainting.maskDilation != null ? `${stepSettings.inpainting.maskDilation} px` : "—"}</dd>
                                </div>
                                {stepSettings.inpainting.precision && (
                                  <div>
                                    <dt className="text-zinc-400">Precision</dt>
                                    <dd className="font-mono text-zinc-200">{stepSettings.inpainting.precision}</dd>
                                  </div>
                                )}
                              </dl>
                            </div>

                            {/* Rendering Card */}
                            <div className="rounded-lg border border-white/10 bg-black/20 p-2.5">
                              <div className="mb-2 flex items-center gap-1.5 font-semibold text-zinc-200">
                                <Icon icon="carbon:text-font" className="h-3.5 w-3.5 text-indigo-300" />
                                <span>Rendering & Typesetting</span>
                              </div>
                              <dl className="grid grid-cols-2 gap-x-2 gap-y-1.5 text-[11px]">
                                <div>
                                  <dt className="text-zinc-400">Renderer</dt>
                                  <dd className="font-medium text-white capitalize">{stepSettings.rendering.rendererLabel}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Direction</dt>
                                  <dd className="font-medium text-zinc-200 capitalize">{stepSettings.rendering.direction}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Alignment</dt>
                                  <dd className="font-medium text-zinc-200 capitalize">{stepSettings.rendering.alignment}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Lettering Case</dt>
                                  <dd className="font-medium text-zinc-200">{stepSettings.rendering.letterCaseLabel}</dd>
                                </div>
                                <div>
                                  <dt className="text-zinc-400">Font</dt>
                                  <dd className="font-medium text-zinc-200 truncate" title={stepSettings.rendering.font || "Sans-serif"}>{stepSettings.rendering.font || "Sans-serif"}</dd>
                                </div>
                              </dl>
                            </div>

                            {/* Colorization & Upscaling Card */}
                            {(stepSettings.colorization?.enabled || stepSettings.upscaling?.enabled) ? (
                              <div className="rounded-lg border border-white/10 bg-black/20 p-2.5">
                                <div className="mb-2 flex items-center gap-1.5 font-semibold text-zinc-200">
                                  <Icon icon="carbon:color-palette" className="h-3.5 w-3.5 text-indigo-300" />
                                  <span>Colorization & Upscaling</span>
                                </div>
                                <dl className="grid grid-cols-2 gap-x-2 gap-y-1.5 text-[11px]">
                                  {stepSettings.colorization?.enabled && (
                                    <>
                                      <div>
                                        <dt className="text-zinc-400">Colorizer</dt>
                                        <dd className="font-medium text-white truncate">{stepSettings.colorization.colorizerLabel}</dd>
                                      </div>
                                      <div>
                                        <dt className="text-zinc-400">Color size</dt>
                                        <dd className="font-mono text-zinc-200">{stepSettings.colorization.size || "—"}</dd>
                                      </div>
                                    </>
                                  )}
                                  {stepSettings.upscaling?.enabled && (
                                    <>
                                      <div>
                                        <dt className="text-zinc-400">Upscaler</dt>
                                        <dd className="font-medium text-white truncate">{stepSettings.upscaling.upscalerLabel}</dd>
                                      </div>
                                      <div>
                                        <dt className="text-zinc-400">Upscale ratio</dt>
                                        <dd className="font-mono text-zinc-200">{stepSettings.upscaling.ratio || "—"}</dd>
                                      </div>
                                    </>
                                  )}
                                </dl>
                              </div>
                            ) : null}
                          </div>
                        </div>
);
