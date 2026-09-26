import React from 'react';
import { Icon } from '@iconify/react';
import type { ProfessionalTranslationAudit } from '../ProfessionalAudit';

const formatProfessionalAnalysisValue = (value: unknown): string => {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(formatProfessionalAnalysisValue).filter(Boolean).join(' · ');
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, entry]) => `${key}: ${formatProfessionalAnalysisValue(entry)}`)
      .filter(Boolean)
      .join(' · ');
  }
  return String(value);
};

interface StoryAnalysisTabProps {
  professionalAudit: ProfessionalTranslationAudit | null;
}

export const StoryAnalysisTab: React.FC<StoryAnalysisTabProps> = ({ professionalAudit }) => (
<div>
                          {professionalAudit?.analysis?.stories?.length ? (
                            <section className="rounded-lg border border-indigo-400/25 bg-indigo-500/10 p-3" aria-label="Professional story analysis">
                              <div className="mb-2.5 flex items-center justify-between gap-2">
                                <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-indigo-200">
                                  <Icon icon="carbon:document" className="h-4 w-4 text-indigo-300" />
                                  Story analysis
                                </h3>
                                <span className="text-[10px] text-indigo-200/80">
                                  {professionalAudit.analysis.stories.length} {professionalAudit.analysis.stories.length === 1 ? "story" : "stories"}
                                </span>
                              </div>
                              <div className="space-y-3 pr-1">
                                {professionalAudit.analysis.stories.map((story, index) => {
                                  const confidence = typeof story.confidence === "number" ? story.confidence : null;
                                  const details = ([
                                    ["Characters", story.characters],
                                    ["Relationships", story.relationships],
                                    ["Honorific policy", story.honorific_policy],
                                    ["Language features", story.language_features],
                                    ["Localization conventions", story.localization_conventions],
                                    ["Voice notes", story.voice_notes],
                                    ["Continuity", story.continuity],
                                    ["Glossary", story.glossary],
                                    ["Ambiguities", story.ambiguities],
                                  ] as Array<[string, unknown]>)
                                    .map(([label, value]): [string, string] => [label, formatProfessionalAnalysisValue(value)])
                                    .filter(([, value]) => value);
                                  return (
                                    <article key={`${story.start_page}-${story.end_page}-${index}`} className="border-b border-white/10 pb-3 last:border-0 last:pb-0">
                                      <div className="mb-1.5 flex items-center justify-between gap-2">
                                        <h4 className="text-xs font-semibold text-white">
                                          Story {index + 1}
                                          {(story.start_page || story.end_page) && (
                                            <span className="ml-1.5 font-normal text-indigo-200/80">
                                              · Pages {story.start_page ?? "?"}–{story.end_page ?? "?"}
                                            </span>
                                          )}
                                        </h4>
                                        {confidence !== null && (
                                          <span className="shrink-0 rounded-full bg-indigo-400/15 px-1.5 py-0.5 text-[10px] font-medium text-indigo-200">
                                            {Math.round(confidence * 100)}% confidence
                                          </span>
                                        )}
                                      </div>
                                      {story.summary && <p className="text-xs leading-5 text-zinc-200">{story.summary}</p>}
                                      {details.length > 0 && (
                                        <dl className="mt-2 space-y-1.5 text-[11px]">
                                          {details.map(([label, value]) => (
                                            <div key={label}>
                                              <dt className="text-indigo-200/70">{label}</dt>
                                              <dd className="whitespace-pre-wrap text-zinc-300">{value}</dd>
                                            </div>
                                          ))}
                                        </dl>
                                      )}
                                    </article>
                                  );
                                })}
                              </div>
                            </section>
                          ) : (
                            <div className="flex flex-col items-center justify-center py-12 text-center text-xs text-zinc-500">
                              <Icon icon="carbon:document" className="mb-3 h-8 w-8 text-zinc-600" />
                              <p>No story analysis data available for this page.</p>
                            </div>
                          )}
                        </div>
);
