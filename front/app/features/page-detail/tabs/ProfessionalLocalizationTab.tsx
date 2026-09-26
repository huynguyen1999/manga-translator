import React from 'react';
import { Icon } from '@iconify/react';
import type { ProfessionalTranslationAudit } from '../ProfessionalAudit';

interface ProfessionalLocalizationTabProps {
  professionalAudit: ProfessionalTranslationAudit | null;
}

export const ProfessionalLocalizationTab: React.FC<ProfessionalLocalizationTabProps> = ({ professionalAudit }) => (
<div>
                          {professionalAudit?.regions?.length ? (
                            <section className="rounded-lg border border-amber-400/25 bg-amber-500/10 p-3" aria-label="Professional localization audit">
                              <div className="mb-2.5 flex items-center justify-between gap-2">
                                <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-amber-200">
                                  <Icon icon="carbon:translate" className="h-4 w-4 text-amber-300" />
                                  Professional localization
                                </h3>
                                <span className="text-[10px] text-amber-200/80">Source · Draft · Final</span>
                              </div>
                              <div className="space-y-2 pr-1">
                                {professionalAudit.regions.map((region, index) => {
                                  const confidence = typeof region.confidence === "number" ? region.confidence : null;
                                  const reasons = region.review_reasons?.filter(Boolean) || [];
                                  return (
                                    <div key={region.id || index} className="rounded-md border border-white/10 bg-black/20 p-2 text-[11px]">
                                      <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-zinc-500">Region {index + 1}</div>
                                      <dl className="space-y-1.5">
                                        <div>
                                          <dt className="text-zinc-500">Japanese source</dt>
                                          <dd className="whitespace-pre-wrap text-zinc-300">{region.source || "—"}</dd>
                                        </div>
                                        <div>
                                          <dt className="text-amber-300/80">First draft</dt>
                                          <dd className="whitespace-pre-wrap text-amber-100">{region.draft || "—"}</dd>
                                        </div>
                                        <div>
                                          <dt className="text-emerald-300/80">Editor pass</dt>
                                          <dd className="whitespace-pre-wrap text-emerald-100">{region.final || "—"}</dd>
                                        </div>
                                      </dl>
                                      {(confidence !== null || reasons.length > 0) && (
                                        <div className="mt-1.5 border-t border-white/10 pt-1.5 text-[10px] text-zinc-400">
                                          {confidence !== null && <span>Confidence {Math.round(confidence * 100)}%</span>}
                                          {reasons.length > 0 && <span>{confidence !== null ? " · " : ""}{reasons.join(" · ")}</span>}
                                        </div>
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                            </section>
                          ) : (
                            <div className="flex flex-col items-center justify-center py-12 text-center text-xs text-zinc-500">
                              <Icon icon="carbon:translate" className="mb-3 h-8 w-8 text-zinc-600" />
                              <p>No professional localization data available for this page.</p>
                            </div>
                          )}
                        </div>
);
