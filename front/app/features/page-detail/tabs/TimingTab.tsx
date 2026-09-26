import React from "react";
import { Icon } from "@iconify/react";
import type { PipelineRunManifest } from "@/types";
import type { PipelineDetailsSidebarState } from "@/features/page-detail/PipelineDetailsSidebar";
import { formatElapsedTime } from "@/utils/pageDetailSettings";
import {
  formatTimestamp,
  resolveStagesToRetry,
  resolveTranslationTiming,
  timestampTooltip,
} from "@/utils/pageDetailTiming";

interface TimingTabProps {
  timing: ReturnType<typeof resolveTranslationTiming>;
  pipelineManifest: PipelineRunManifest | null;
  isPipelineTimingLoading: boolean;
  isTranslationDetailLoading: boolean;
  canRetryFromStage: boolean;
  sidebar: PipelineDetailsSidebarState;
  onQueueRetryFromStage: (stageId: string) => Promise<void>;
}

export const TimingTab: React.FC<TimingTabProps> = ({
  timing,
  pipelineManifest,
  isPipelineTimingLoading,
  isTranslationDetailLoading,
  canRetryFromStage,
  sidebar,
  onQueueRetryFromStage,
}) => {
  const selectedRetryStageData = pipelineManifest?.stages.find(
    (stage) => stage.id === sidebar.selectedRetryStage,
  );
  const stagesToRetry = sidebar.selectedRetryStage
    ? resolveStagesToRetry(pipelineManifest?.stages, sidebar.selectedRetryStage)
    : [];

  return (
    <>
      {sidebar.tab === "timing" && (
        <div className="space-y-3">
          {/* Start / End / Duration */}
          <section
            className="rounded-lg border border-indigo-400/20 bg-indigo-500/10 p-2.5"
            aria-label="Translation timing"
          >
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-indigo-200">
                <Icon icon="carbon:time" className="h-4 w-4 text-indigo-300" />
                Timing overview
              </h3>
              {isTranslationDetailLoading && (
                <Icon
                  icon="carbon:renew"
                  className="h-3.5 w-3.5 animate-spin text-indigo-300"
                  aria-label="Loading"
                />
              )}
            </div>
            <dl className="grid grid-cols-3 gap-x-3 text-xs">
              <div>
                <dt className="text-zinc-400">Started at</dt>
                <dd
                  className="mt-0.5 font-mono font-medium text-white"
                  title={timestampTooltip(timing.startAt)}
                >
                  <time
                    dateTime={
                      timing.startAt instanceof Date
                        ? timing.startAt.toISOString()
                        : timing.startAt != null
                          ? String(timing.startAt)
                          : undefined
                    }
                  >
                    {formatTimestamp(timing.startAt)}
                  </time>
                </dd>
              </div>
              <div>
                <dt className="text-zinc-400">Ended at</dt>
                <dd
                  className="mt-0.5 font-mono font-medium text-white"
                  title={timestampTooltip(timing.endAt)}
                >
                  <time
                    dateTime={
                      timing.endAt instanceof Date
                        ? timing.endAt.toISOString()
                        : timing.endAt != null
                          ? String(timing.endAt)
                          : undefined
                    }
                  >
                    {formatTimestamp(timing.endAt)}
                  </time>
                </dd>
              </div>
              <div>
                <dt className="text-zinc-400">Total duration</dt>
                <dd className="mt-0.5 font-mono text-base font-semibold text-indigo-100">
                  {formatElapsedTime(timing.durationMs)}
                </dd>
              </div>
            </dl>
          </section>

          {/* Per-stage breakdown */}
          <div>
            <div className="mb-1.5 flex items-center justify-between gap-3">
              <div>
                <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-zinc-200">
                  <Icon
                    icon="carbon:flow"
                    className="h-3.5 w-3.5 text-indigo-300"
                  />
                  Per-stage breakdown
                </h3>
              </div>
              {pipelineManifest && (
                <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium capitalize text-emerald-300">
                  {pipelineManifest.status}
                </span>
              )}
            </div>
            {isPipelineTimingLoading ? (
              <div className="flex items-center gap-2 py-4 text-xs text-zinc-400">
                <Icon
                  icon="carbon:renew"
                  className="h-4 w-4 animate-spin text-indigo-300"
                />
                Loading timings…
              </div>
            ) : pipelineManifest?.stages?.length ? (
              <>
                <div className="space-y-0.5">
                  {pipelineManifest.stages.map((stage) => {
                    const canRetry = Boolean(
                      canRetryFromStage &&
                      stage.id !== "input" &&
                      stage.status !== "skipped" &&
                      !["running", "paused"].includes(pipelineManifest.status),
                    );
                    return (
                      <div
                        key={stage.id}
                        className={`flex items-center justify-between gap-2 rounded-md px-2 py-1 ${
                          sidebar.selectedRetryStage === stage.id
                            ? "bg-indigo-500/15 ring-1 ring-indigo-400/40"
                            : "bg-black/20"
                        }`}
                      >
                        <div className="flex min-w-0 flex-1 items-baseline gap-2">
                          <span
                            className="truncate text-xs font-medium text-zinc-200"
                            title={stage.label || stage.id}
                          >
                            {stage.label || stage.id}
                          </span>
                          <span className="shrink-0 text-[10px] capitalize text-zinc-400">
                            {stage.status}
                          </span>
                        </div>
                        <div className="flex shrink-0 items-center gap-1.5">
                          <span className="font-mono text-xs text-indigo-200">
                            {formatElapsedTime(stage.durationMs)}
                          </span>
                          {canRetryFromStage &&
                            stage.id !== "input" &&
                            stage.status !== "skipped" && (
                              <button
                                type="button"
                                onClick={() => {
                                  sidebar.setSelectedRetryStage(stage.id);
                                  sidebar.setRetryFromStageError(null);
                                }}
                                disabled={
                                  !canRetry || sidebar.isRetryingFromStage
                                }
                                aria-pressed={
                                  sidebar.selectedRetryStage === stage.id
                                }
                                aria-label={`Retry from ${stage.label || stage.id}`}
                                title="Choose this stage and its downstream stages to run again"
                                className="inline-flex min-h-7 min-w-7 items-center justify-center rounded-md border border-indigo-400/30 text-indigo-200 transition-colors hover:bg-indigo-500/20 focus-visible:outline-2 focus-visible:outline-indigo-300 disabled:cursor-not-allowed disabled:opacity-50"
                              >
                                <Icon icon="carbon:renew" className="h-3.5 w-3.5" />
                              </button>
                            )}
                        </div>
                      </div>
                    );
                  })}
                </div>
                {sidebar.selectedRetryStage &&
                  selectedRetryStageData &&
                  canRetryFromStage && (
                    <section
                      className="mt-3 rounded-lg border border-indigo-400/30 bg-indigo-500/10 p-3"
                      aria-label="Stage retry plan"
                    >
                      <h4 className="text-xs font-semibold text-indigo-100">
                        Retry from{" "}
                        {selectedRetryStageData.label ||
                          selectedRetryStageData.id}
                        ?
                      </h4>
                      <p className="mt-1 text-xs leading-5 text-indigo-100">
                        Stages to run:{" "}
                        {stagesToRetry.join(" → ") || "No stages available"}.
                      </p>
                      <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => sidebar.setSelectedRetryStage(null)}
                          disabled={sidebar.isRetryingFromStage}
                          className="min-h-8 rounded-md px-2.5 text-xs font-medium text-zinc-300 hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-zinc-300 disabled:opacity-50"
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            sidebar.setIsRetryingFromStage(true);
                            sidebar.setRetryFromStageError(null);
                            void onQueueRetryFromStage(
                              sidebar.selectedRetryStage!,
                            )
                              .then(() => {
                                sidebar.setSelectedRetryStage(null);
                              })
                              .catch((error) => {
                                sidebar.setRetryFromStageError(
                                  error instanceof Error
                                    ? error.message
                                    : "Could not queue this stage retry.",
                                );
                              })
                              .finally(() =>
                                sidebar.setIsRetryingFromStage(false),
                              );
                          }}
                          disabled={
                            !stagesToRetry.length || sidebar.isRetryingFromStage
                          }
                          aria-busy={sidebar.isRetryingFromStage}
                          className="inline-flex min-h-8 items-center gap-1.5 rounded-md bg-indigo-600 px-3 text-xs font-semibold text-white transition-colors hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-indigo-300 disabled:cursor-wait disabled:opacity-60"
                        >
                          <Icon
                            icon="carbon:renew"
                            className={`h-3.5 w-3.5 ${sidebar.isRetryingFromStage ? "animate-spin" : ""}`}
                          />
                          {sidebar.isRetryingFromStage
                            ? "Queueing…"
                            : "Queue retry"}
                        </button>
                      </div>
                      {sidebar.retryFromStageError && (
                        <p className="mt-2 text-xs text-rose-300" role="alert">
                          {sidebar.retryFromStageError}
                        </p>
                      )}
                    </section>
                  )}
              </>
            ) : (
              <div className="py-2 text-xs leading-5 text-zinc-400">
                No pipeline timing data is available for this page.
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
};
