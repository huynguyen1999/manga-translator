import { Icon } from '@iconify/react';
import { Link } from 'react-router';
import type { MangaSummary } from '@/types';
import { summaryJobStageLabel } from '@/utils/summaryJobs';

export interface MangaSummaryModalState {
  title: string;
  data: MangaSummary | null;
  loading: boolean;
  error: string | null;
}

interface MangaSummaryModalProps {
  summaryState: MangaSummaryModalState;
  summaryCopied: boolean;
  isSummarizing: boolean;
  detailUrl: string;
  onClose: () => void;
  onCopy: () => void;
  onResume: () => void | Promise<void>;
  onPause: () => void | Promise<void>;
  onStop: () => void | Promise<void>;
  onSummarize: (refreshText: boolean) => void;
}

export function MangaSummaryModal({
  summaryState,
  summaryCopied,
  isSummarizing,
  detailUrl,
  onClose,
  onCopy,
  onResume,
  onPause,
  onStop,
  onSummarize,
}: MangaSummaryModalProps) {
  return (
    <div
      className="fixed inset-0 z-60 flex items-center justify-center bg-black/70 p-4 isolate"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="manga-summary-title"
        className="w-full max-w-2xl rounded-2xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-700 dark:bg-zinc-900 transform-gpu"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600 dark:text-indigo-400">Manga synopsis</p>
            <h3 id="manga-summary-title" className="mt-1 truncate text-lg font-semibold text-zinc-900 dark:text-zinc-100">
              {summaryState.title}
            </h3>
          </div>
          <div className="flex items-center gap-2">
            {summaryState.loading && (
              <button
                type="button"
                onClick={onClose}
                className="flex items-center space-x-1 rounded-lg border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-950/60 px-2.5 py-1 text-xs font-semibold text-indigo-700 dark:text-indigo-300 hover:bg-indigo-100 dark:hover:bg-indigo-900/60 transition-colors cursor-pointer"
                title="Continue generation in the background"
              >
                <Icon icon="carbon:minimize" className="w-3.5 h-3.5" />
                <span>Run in background</span>
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
              title="Close summary"
              aria-label="Close summary"
            >
              <Icon icon="carbon:close" className="h-5 w-5" />
            </button>
          </div>
        </div>

        <div className="max-h-[60vh] overflow-y-auto px-5 py-5 synopsis-scroll-container overscroll-contain">
          {summaryState.loading ? (
            <div className="flex flex-col items-center justify-center gap-3 py-16 text-sm text-zinc-500 dark:text-zinc-400" aria-live="polite">
              <div className="flex items-center gap-2">
                {summaryState.data?.jobStatus === 'paused' ? (
                  <Icon icon="carbon:pause-outline" className="h-5 w-5 text-amber-500" />
                ) : (
                  <Icon icon="carbon:renew" className="h-5 w-5 animate-spin text-indigo-500" />
                )}
                <span>
                  {summaryState.data?.jobStatus === 'paused'
                    ? 'Synopsis generation paused'
                    : summaryState.data?.jobStage === 'summarizing'
                    ? 'Synthesizing manga synopsis…'
                    : summaryState.data?.summary
                    ? 'Regenerating synopsis…'
                    : 'Generating synopsis…'}
                </span>
              </div>
              {typeof summaryState.data?.jobProgress === 'number' && summaryState.data.jobProgress > 0 && (
                <div className="w-48 bg-zinc-200 dark:bg-zinc-700 rounded-full h-1.5 overflow-hidden">
                  <div
                    className="bg-indigo-600 h-1.5 rounded-full transition-all duration-300"
                    style={{ width: `${Math.min(100, Math.max(0, summaryState.data.jobProgress))}%` }}
                  />
                </div>
              )}
              {summaryState.data?.jobStage && (
                <div className="w-full max-w-sm rounded-lg border border-zinc-200 bg-white/80 px-3 py-2 text-center dark:border-zinc-700 dark:bg-zinc-900/80" aria-live="polite">
                  <p className="text-xs font-semibold text-zinc-700 dark:text-zinc-200">
                    Current step · {summaryJobStageLabel(summaryState.data.jobStage)}
                  </p>
                  {['detecting', 'ocr', 'textline_merge'].includes(summaryState.data.jobStage) && summaryState.data.jobPageCount != null ? (
                    <p className="mt-1 text-xs tabular-nums text-zinc-500 dark:text-zinc-400">
                      {summaryState.data.jobStagePassedCount ?? 0} of {summaryState.data.jobPageCount} pages done in this step
                      {summaryState.data.jobPagesWithText != null && ` · ${summaryState.data.jobPagesWithText} with text`}
                    </p>
                  ) : summaryState.data.jobMessage ? (
                    <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">{summaryState.data.jobMessage}</p>
                  ) : null}
                </div>
              )}
              <div className="flex items-center gap-2 mt-2">
                {summaryState.data?.jobStatus === 'paused' ? (
                  <button
                    type="button"
                    onClick={() => void onResume()}
                    className="inline-flex items-center gap-1 rounded-lg border border-indigo-300 dark:border-indigo-700 bg-white dark:bg-zinc-800 px-3 py-1.5 text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 cursor-pointer"
                  >
                    <Icon icon="carbon:play" className="h-3.5 w-3.5" />
                    <span>Resume</span>
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void onPause()}
                    className="inline-flex items-center gap-1 rounded-lg border border-amber-300 dark:border-amber-700 bg-white dark:bg-zinc-800 px-3 py-1.5 text-xs font-semibold text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-950/40 cursor-pointer"
                  >
                    <Icon icon="carbon:pause" className="h-3.5 w-3.5" />
                    <span>Pause</span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => void onStop()}
                  className="inline-flex items-center gap-1 rounded-lg border border-rose-300 dark:border-rose-700 bg-white dark:bg-zinc-800 px-3 py-1.5 text-xs font-semibold text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/40 cursor-pointer"
                >
                  <Icon icon="carbon:stop-filled-alt" className="h-3.5 w-3.5" />
                  <span>Stop</span>
                </button>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer font-medium mt-1"
              >
                Minimize and continue in background
              </button>
            </div>
          ) : summaryState.data?.summary ? (
            <>
              {summaryState.data.stale && (
                <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200" role="status">
                  This synopsis is outdated because the manga text changed.
                </div>
              )}
              {summaryState.error && (
                <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-900 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-200" role="alert">
                  {summaryState.error}
                </div>
              )}
              <p className="whitespace-pre-wrap text-sm leading-7 text-zinc-700 dark:text-zinc-200">
                {summaryState.data.summary}
              </p>
              <p className="mt-5 text-xs text-zinc-500 dark:text-zinc-400">
                {summaryState.data.pageCount} pages · {summaryState.data.textPageCount} with original text
                {summaryState.data.language ? ` · ${summaryState.data.language}` : ''}
                {summaryState.data.model ? ` · ${summaryState.data.model}` : ''}
                {summaryState.data.skippedPages?.length ? ` · ${summaryState.data.skippedPages.length} page${summaryState.data.skippedPages.length === 1 ? '' : 's'} skipped` : ''}
              </p>
              {summaryState.data.skippedPages?.length ? (
                <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                  Skipped: {summaryState.data.skippedPages.join(', ')}
                </p>
              ) : null}
            </>
          ) : (
            <div className="py-8 text-sm text-zinc-600 dark:text-zinc-300" role="alert">
              <p>{summaryState.error || 'No synopsis is available yet.'}</p>
            </div>
          )}
        </div>

        {!summaryState.loading && (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-200 px-5 py-4 dark:border-zinc-800">
            <Link
              to={detailUrl}
              className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              <Icon icon="carbon:launch" className="h-3.5 w-3.5" /> Open manga details
            </Link>
            <div className="flex flex-wrap items-center justify-end gap-2">
              {summaryState.data?.summary && (
                <button
                  type="button"
                  onClick={onCopy}
                  className="rounded-lg border border-zinc-200 bg-white px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
                >
                  <Icon icon={summaryCopied ? 'carbon:checkmark' : 'carbon:copy'} className="mr-1 inline h-3.5 w-3.5" />
                  {summaryCopied ? 'Copied' : 'Copy'}
                </button>
              )}
              <button
                type="button"
                onClick={() => onSummarize(false)}
                disabled={isSummarizing}
                className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-60"
              >
                <Icon icon="carbon:renew" className="mr-1 inline h-3.5 w-3.5" />
                {summaryState.data?.summary ? 'Regenerate' : 'Try again'}
              </button>
              <button
                type="button"
                onClick={() => onSummarize(true)}
                disabled={isSummarizing}
                className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-700 hover:bg-indigo-100 disabled:opacity-60 dark:border-indigo-800 dark:bg-indigo-950/50 dark:text-indigo-300 dark:hover:bg-indigo-900/60"
                title="Run detection, OCR, text merging, and cleanup again for every page, then overwrite the saved text and summary"
              >
                <Icon icon="carbon:reset" className="mr-1 inline h-3.5 w-3.5" />
                Re-read & regenerate
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
