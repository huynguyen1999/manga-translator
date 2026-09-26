import React from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { Icon } from '@iconify/react';
import type { FinishedImage } from '@/types';
import { MANGA_TITLE_MAX_LENGTH } from '@/config';
import { mangaIdForTitle } from '@/utils/routeState';
import { getStoredMangaReadProgress } from '@/utils/resultGallery';
import { MangaGroupThumbnail } from './MangaGroupThumbnail';
import { MangaReadBadge } from './MangaCard';
import type { SummaryAvailabilityState } from './useMangaSummaryActions';

type MangaDetailGroup = {
  id: string;
  title: string;
  count: number;
  coverImage: FinishedImage | null;
  images: FinishedImage[];
  seriesId?: string | null;
  seriesTitle?: string | null;
  needsReviewCount: number;
};

type AssigningManga = {
  id: string;
  title: string;
  seriesId?: string | null;
  seriesTitle?: string | null;
};

type SummaryAvailability = { title: string; state: SummaryAvailabilityState };

interface MangaDetailHeaderProps {
  currentSingleGroup: MangaDetailGroup;
  closeMangaDetail: () => void;
  renamingManga: string | null;
  renameInputValue: string;
  setRenameInputValue: Dispatch<SetStateAction<string>>;
  handleSaveRename: (title: string) => void;
  setRenamingManga: Dispatch<SetStateAction<string | null>>;
  handleStartRename: (title: string) => void;
  setAssigningManga: Dispatch<SetStateAction<AssigningManga | null>>;
  summaryAvailability: SummaryAvailability | null;
  onRestoreBatchPages?: (groupId: string, title: string) => Promise<number>;
  handleRestoreBatchPages: () => Promise<void>;
  restoringBatchPages: string | null;
  handleToggleSelectAll: (title: string, images?: FinishedImage[]) => Promise<void>;
  selectedImageIds: Set<string>;
  onRerenderImages?: (images: FinishedImage[]) => void | Promise<void>;
  requestRerender: (images: FinishedImage[]) => void;
  handleReadManga: (title: string, images?: FinishedImage[]) => Promise<void>;
  readerLoadingTitle: string | null;
  readerLoadError: string | null;
  handleSummarize: (title: string) => Promise<void>;
  summarizingTitle: string | null;
  handleDownloadCbz: (title: string, images: FinishedImage[], original?: boolean) => Promise<void>;
  downloadingCbz: Record<string, boolean>;
  onDeleteManga?: (images: FinishedImage[], mangaTitle?: string) => void | Promise<void>;
  setConfirmDeleteManga: Dispatch<SetStateAction<string | null>>;
  restoreBatchMessage: string | null;
  reviewOnly: boolean;
  handleCardEdit: (image: FinishedImage) => void;
}

export const MangaDetailHeader: React.FC<MangaDetailHeaderProps> = ({
  currentSingleGroup,
  closeMangaDetail,
  renamingManga,
  renameInputValue,
  setRenameInputValue,
  handleSaveRename,
  setRenamingManga,
  handleStartRename,
  setAssigningManga,
  summaryAvailability,
  onRestoreBatchPages,
  handleRestoreBatchPages,
  restoringBatchPages,
  handleToggleSelectAll,
  selectedImageIds,
  onRerenderImages,
  requestRerender,
  handleReadManga,
  readerLoadingTitle,
  readerLoadError,
  handleSummarize,
  summarizingTitle,
  handleDownloadCbz,
  downloadingCbz,
  onDeleteManga,
  setConfirmDeleteManga,
  restoreBatchMessage,
  reviewOnly,
  handleCardEdit,
}) => (
<div className="rounded-2xl border border-zinc-200 bg-white p-4 shadow-xs dark:border-zinc-800 dark:bg-zinc-900/70">
            <div className="flex min-w-0 flex-wrap items-start gap-4">
              <button
                type="button"
                onClick={closeMangaDetail}
                className="inline-flex h-10 shrink-0 items-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 text-xs font-semibold text-zinc-700 shadow-2xs transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700 cursor-pointer"
                title="Return to all manga cards"
              >
                <Icon icon="carbon:arrow-left" className="w-3.5 h-3.5" />
                <span>All Manga</span>
              </button>

              {currentSingleGroup.coverImage && (
                <MangaGroupThumbnail image={currentSingleGroup.coverImage} />
              )}

              <div className="min-w-0 flex-1">
                {renamingManga === currentSingleGroup.title ? (
                  <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="text"
                      value={renameInputValue}
                      onChange={(e) => setRenameInputValue(e.target.value)}
                      maxLength={MANGA_TITLE_MAX_LENGTH}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleSaveRename(currentSingleGroup.title);
                        if (e.key === 'Escape') setRenamingManga(null);
                      }}
                      autoFocus
                      className="min-w-0 max-w-full rounded border border-indigo-400 bg-white px-2 py-1 text-sm font-semibold text-zinc-900 dark:border-indigo-600 dark:bg-zinc-900 dark:text-zinc-100"
                    />
                    <button
                      type="button"
                      onClick={() => handleSaveRename(currentSingleGroup.title)}
                      className="rounded-md p-1.5 text-emerald-600 hover:bg-emerald-50 hover:text-emerald-700 dark:text-emerald-400 dark:hover:bg-emerald-950/40 cursor-pointer"
                      title="Save title"
                    >
                      <Icon icon="carbon:checkmark" className="w-4 h-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setRenamingManga(null)}
                      className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 cursor-pointer"
                      title="Cancel"
                    >
                      <Icon icon="carbon:close" className="w-4 h-4" />
                    </button>
                  </div>
                ) : (
                  <div className="flex min-w-0 items-center gap-1.5">
                    <h3
                      className="min-w-0 truncate text-base font-bold text-zinc-900 transition-colors hover:text-indigo-600 dark:text-zinc-100 dark:hover:text-indigo-400 cursor-pointer"
                      onClick={() => handleStartRename(currentSingleGroup.title)}
                      title="Click to rename Manga"
                    >
                      {currentSingleGroup.title}
                    </h3>
                    <button
                      type="button"
                      onClick={() => handleStartRename(currentSingleGroup.title)}
                      className="shrink-0 rounded-md p-1.5 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-300 cursor-pointer"
                      title="Rename Manga"
                    >
                      <Icon icon="carbon:edit" className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}

                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                    {currentSingleGroup.count} {currentSingleGroup.count === 1 ? 'page' : 'pages'}
                  </span>
                  {(() => {
                    const currentSingleReadProgress = getStoredMangaReadProgress(currentSingleGroup.title, currentSingleGroup.count);
                    return <MangaReadBadge progress={currentSingleReadProgress} pageCount={currentSingleGroup.count} />;
                  })()}
                  {currentSingleGroup.seriesTitle ? (
                    <button
                      type="button"
                      onClick={() => setAssigningManga({
                        id: currentSingleGroup.id || mangaIdForTitle(currentSingleGroup.title),
                        title: currentSingleGroup.title,
                        seriesId: currentSingleGroup.seriesId,
                        seriesTitle: currentSingleGroup.seriesTitle,
                      })}
                      className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2.5 py-1 text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-200 dark:bg-indigo-950/60 dark:text-indigo-300 dark:hover:bg-indigo-900/80 cursor-pointer"
                      title={`In series: ${currentSingleGroup.seriesTitle}. Click to change or move series`}
                    >
                      <Icon icon="carbon:catalog" className="h-3.5 w-3.5" />
                      <span>Series: {currentSingleGroup.seriesTitle}</span>
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setAssigningManga({
                        id: currentSingleGroup.id || mangaIdForTitle(currentSingleGroup.title),
                        title: currentSingleGroup.title,
                        seriesId: null,
                        seriesTitle: null,
                      })}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-white px-2.5 py-1 text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-50 dark:border-indigo-800 dark:bg-zinc-800 dark:text-indigo-300 dark:hover:bg-indigo-950/50 cursor-pointer"
                      title="Add this manga to a series"
                    >
                      <Icon icon="carbon:add" className="h-3.5 w-3.5" />
                      <span>Add to Series</span>
                    </button>
                  )}
                  {summaryAvailability?.title === currentSingleGroup.title && (
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ${
                        summaryAvailability.state === 'summarized'
                          ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300'
                            : summaryAvailability.state === 'queued' || summaryAvailability.state === 'generating'
                              ? 'bg-indigo-100 text-indigo-800 dark:bg-indigo-950/60 dark:text-indigo-300'
                            : summaryAvailability.state === 'paused'
                              ? 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300'
                            : summaryAvailability.state === 'stale' || summaryAvailability.state === 'error'
                              ? 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300'
                              : 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300'
                      }`}
                      role="status"
                      title={summaryAvailability.state === 'stale' ? 'The manga text changed since this summary was generated.' : undefined}
                    >
                      <Icon
                        icon={summaryAvailability.state === 'summarized' ? 'carbon:checkmark-filled' : summaryAvailability.state === 'queued' ? 'carbon:time' : summaryAvailability.state === 'generating' ? 'carbon:renew' : summaryAvailability.state === 'paused' ? 'carbon:pause-outline' : 'carbon:document'}
                        className={`h-3 w-3 ${summaryAvailability.state === 'generating' ? 'animate-spin' : ''}`}
                      />
                      {summaryAvailability.state === 'summarized'
                        ? 'Summarized'
                        : summaryAvailability.state === 'not-summarized'
                          ? 'Not summarized'
                            : summaryAvailability.state === 'queued'
                              ? 'Summary queued'
                              : summaryAvailability.state === 'generating'
                              ? 'Summary in progress'
                              : summaryAvailability.state === 'paused'
                              ? 'Summary paused'
                            : summaryAvailability.state === 'stale'
                              ? 'Summary needs update'
                              : summaryAvailability.state === 'error'
                                ? 'Summary failed'
                                : summaryAvailability.state === 'loading'
                                  ? 'Checking summary'
                                  : 'Summary status unavailable'}
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Actions: Select All, Read, Download CBZ, Delete */}
            <div className="mt-4 flex flex-col gap-3 border-t border-zinc-200 pt-4 dark:border-zinc-800 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap items-center gap-2">
                {onRestoreBatchPages && (
                  <button
                    type="button"
                    onClick={() => void handleRestoreBatchPages()}
                    disabled={restoringBatchPages === currentSingleGroup.id}
                    className="inline-flex h-10 items-center gap-2 rounded-lg border border-indigo-200 bg-white px-3 text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-indigo-800 dark:bg-zinc-800 dark:text-indigo-300 dark:hover:bg-indigo-950/50 cursor-pointer"
                    title="Attach completed translation results to this manga"
                    aria-label={`Restore completed batch pages to ${currentSingleGroup.title}`}
                  >
                    <Icon icon={restoringBatchPages === currentSingleGroup.id ? 'carbon:renew' : 'carbon:folder-move-to'} className={`h-4 w-4 ${restoringBatchPages === currentSingleGroup.id ? 'animate-spin' : ''}`} />
                    <span>{restoringBatchPages === currentSingleGroup.id ? 'Restoring…' : 'Restore batch pages'}</span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => handleToggleSelectAll(currentSingleGroup.title, currentSingleGroup.images)}
                  className="inline-flex h-10 items-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 text-xs font-medium text-zinc-700 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700 cursor-pointer"
                >
                  <Icon
                    icon={
                      currentSingleGroup.images.length > 0 &&
                      currentSingleGroup.images.every((img) => selectedImageIds.has(img.id))
                        ? 'carbon:checkbox-checked'
                        : 'carbon:checkbox'
                    }
                    className="h-4 w-4"
                  />
                  <span>
                    {currentSingleGroup.images.length > 0 &&
                    currentSingleGroup.images.every((img) => selectedImageIds.has(img.id))
                      ? 'Deselect All'
                      : 'Select All'}
                  </span>
                </button>

                {onRerenderImages && (
                  <button
                    type="button"
                    onClick={() => requestRerender(currentSingleGroup.images)}
                    className="inline-flex h-10 items-center gap-2 rounded-lg border border-indigo-200 bg-white px-3 text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-50 dark:border-indigo-800 dark:bg-zinc-800 dark:text-indigo-300 dark:hover:bg-indigo-950/50 cursor-pointer"
                    title="Rerun pipeline stages for translated pages in this manga"
                  >
                    <Icon icon="carbon:reset" className="h-4 w-4" />
                    <span>Rerun pipeline…</span>
                  </button>
                )}

                {(() => {
                  const currentSingleReadProgress = getStoredMangaReadProgress(currentSingleGroup.title, currentSingleGroup.count);
                  return (
                    <button
                      type="button"
                      onClick={() => handleReadManga(currentSingleGroup.title, currentSingleGroup.images)}
                      disabled={Boolean(readerLoadingTitle)}
                      className="inline-flex h-10 items-center gap-2 rounded-lg bg-indigo-600 px-3.5 text-xs font-semibold text-white shadow-xs transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 cursor-pointer"
                      title={
                        currentSingleReadProgress.page && !currentSingleReadProgress.complete
                          ? `Continue reading from page ${currentSingleReadProgress.page}`
                          : 'Read manga in continuous scroll mode'
                      }
                      aria-label={
                        readerLoadError === currentSingleGroup.title
                          ? `Retry reading ${currentSingleGroup.title}`
                          : currentSingleReadProgress.complete
                          ? `Read ${currentSingleGroup.title} again`
                          : currentSingleReadProgress.page
                          ? `Continue reading ${currentSingleGroup.title}`
                          : `Read ${currentSingleGroup.title}`
                      }
                    >
                      {readerLoadingTitle === currentSingleGroup.title ? (
                        <Icon icon="carbon:renew" className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Icon icon="carbon:book-open" className="w-3.5 h-3.5" />
                      )}
                      <span>
                        {readerLoadingTitle === currentSingleGroup.title
                          ? 'Loading…'
                          : readerLoadError === currentSingleGroup.title
                          ? 'Retry Read'
                          : currentSingleReadProgress.complete
                          ? 'Read again'
                          : currentSingleReadProgress.page
                          ? 'Continue'
                          : 'Read'}
                      </span>
                    </button>
                  );
                })()}
              </div>

              <div className="flex flex-wrap items-center gap-2 sm:justify-end">

                <button
                  type="button"
                  onClick={() => void handleSummarize(currentSingleGroup.title)}
                  disabled={Boolean(summarizingTitle === currentSingleGroup.title)}
                  className="inline-flex h-10 items-center gap-2 rounded-lg border border-indigo-200 bg-white px-3 text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-indigo-800 dark:bg-zinc-800 dark:text-indigo-300 dark:hover:bg-indigo-950/50 cursor-pointer"
                  title="Summarize the original text in this manga"
                >
                  <Icon
                    icon={summarizingTitle === currentSingleGroup.title ? 'carbon:renew' : 'carbon:document'}
                    className={`w-3.5 h-3.5 ${summarizingTitle === currentSingleGroup.title ? 'animate-spin' : ''}`}
                  />
                  <span>{summarizingTitle === currentSingleGroup.title ? 'Summarizing…' : 'Summary'}</span>
                </button>

                <button
                  type="button"
                  onClick={() => handleDownloadCbz(currentSingleGroup.title, currentSingleGroup.images)}
                  disabled={Boolean(downloadingCbz[currentSingleGroup.title])}
                  className="inline-flex h-10 items-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 text-xs font-semibold text-zinc-700 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700 cursor-pointer"
                  title="Download the translated manga as a CBZ comic archive"
                >
                  {downloadingCbz[currentSingleGroup.title] ? (
                    <>
                      <Icon icon="carbon:renew" className="w-3.5 h-3.5 animate-spin" />
                      <span>Packaging CBZ...</span>
                    </>
                  ) : (
                    <>
                      <Icon icon="carbon:catalog" className="w-3.5 h-3.5" />
                      <span>Translated CBZ</span>
                    </>
                  )}
                </button>

                <button
                  type="button"
                  onClick={() => handleDownloadCbz(currentSingleGroup.title, currentSingleGroup.images, true)}
                  disabled={Boolean(downloadingCbz[currentSingleGroup.title])}
                  className="inline-flex h-10 items-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 text-xs font-semibold text-zinc-700 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700 cursor-pointer"
                  title="Download the original manga pages as a CBZ comic archive"
                >
                  <Icon icon="carbon:download" className="w-3.5 h-3.5" />
                  <span>Original CBZ</span>
                </button>

                {onDeleteManga && (
                  <button
                    type="button"
                    onClick={() => setConfirmDeleteManga(currentSingleGroup.title)}
                    className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-950/50 dark:hover:text-red-400 cursor-pointer"
                    title="Delete this entire manga from library"
                    aria-label="Delete this entire manga from library"
                  >
                    <Icon icon="carbon:trash-can" className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>

            {restoreBatchMessage && (
              <p className={`mt-2 text-xs ${restoreBatchMessage.startsWith('Restored ') ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`} role="status">{restoreBatchMessage}</p>
            )}

            {readerLoadError === currentSingleGroup.title && (
              <div className="mt-3 flex items-center justify-end gap-2 text-xs text-red-400" role="status">
                <span>Couldn’t load all pages.</span>
                <button
                  type="button"
                  onClick={() => handleReadManga(currentSingleGroup.title, currentSingleGroup.images)}
                  className="font-semibold text-red-300 underline underline-offset-2 hover:text-white"
                >
                  Try again
                </button>
              </div>
            )}

            {reviewOnly && currentSingleGroup.needsReviewCount > 0 && (
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-800/70 bg-amber-950/30 px-3.5 py-3 text-amber-100">
                <div className="flex min-w-0 items-start gap-2.5">
                  <Icon icon="carbon:warning-alt" className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
                  <p className="text-xs leading-5 text-amber-200/85">
                    {currentSingleGroup.needsReviewCount} flagged {currentSingleGroup.needsReviewCount === 1 ? 'page' : 'pages'} remain. Open a page, fix the highlighted bubble, then save to approve it.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    const firstReviewPage = currentSingleGroup.images[0];
                    if (firstReviewPage) handleCardEdit(firstReviewPage);
                  }}
                  disabled={!currentSingleGroup.images[0]}
                  className="shrink-0 rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-semibold text-amber-950 transition-colors hover:bg-amber-400 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300"
                >
                  Review next page
                </button>
              </div>
            )}
          </div>
);
