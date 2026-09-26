import React, { type Dispatch, type SetStateAction } from "react";
import { Icon } from "@iconify/react";
import { Link } from "react-router";
import type { FinishedImage } from "@/types";
import { MANGA_TITLE_MAX_LENGTH } from "@/config";
import { buildMangaDetailIdUrl } from "@/utils/routeState";
import type { MangaReadProgress } from "@/utils/resultGallery";
import { MangaGroupThumbnail } from "./MangaGroupThumbnail";
import { MangaReadBadge } from "./MangaCard";
import { RowGroupCards } from "./RowGroupCards";

type MangaRowGroupData = {
  id: string;
  title: string;
  count: number;
  coverImage: FinishedImage | null;
  images: FinishedImage[];
  seriesId?: string | null;
  seriesTitle?: string | null;
  isLoading: boolean;
};

type AssigningManga = {
  id: string;
  title: string;
  seriesId?: string | null;
  seriesTitle?: string | null;
};

interface MangaRowGroupState {
  isCollapsed: boolean;
  isDownloading: boolean;
  isRenaming: boolean;
  allInGroupSelected: boolean;
  readProgress: MangaReadProgress;
  isRowHighlighted: boolean;
  renameInputValue: string;
  selectedImageIds: Set<string>;
  highlightedImageId: string | null;
  readerLoadingTitle: string | null;
  readerLoadError: string | null;
  summarizingTitle: string | null;
  reviewOnly: boolean;
  pageViewState: { from: string };
}

interface MangaRowGroupActions {
  toggleGroupCollapse: (title: string) => void;
  setRenameInputValue: Dispatch<SetStateAction<string>>;
  handleSaveRename: (title: string) => void;
  setRenamingManga: Dispatch<SetStateAction<string | null>>;
  handleStartRename: (title: string) => void;
  setAssigningManga: Dispatch<SetStateAction<AssigningManga | null>>;
  handleToggleSelectAll: (
    title: string,
    images?: FinishedImage[],
  ) => Promise<void>;
  handleReadManga: (
    title: string,
    images?: FinishedImage[],
  ) => void | Promise<void>;
  handleSummarize: (title: string) => void | Promise<void>;
  handleDownloadCbz: (
    title: string,
    images: FinishedImage[],
    original?: boolean,
  ) => void | Promise<void>;
  onDeleteManga?: (
    images: FinishedImage[],
    mangaTitle?: string,
  ) => void | Promise<void>;
  setConfirmDeleteManga: Dispatch<SetStateAction<string | null>>;
  onMoveImage: (image: FinishedImage) => void;
  onReadFromHere: (
    title: string,
    images: FinishedImage[],
    pageIndex: number,
  ) => void;
  onClickImage: (image: FinishedImage) => void;
  onDownloadImage: (image: FinishedImage) => void;
  onDeleteImage?: (image: FinishedImage) => void;
  handleCardDelete: (image: FinishedImage) => void;
  onEditImage: (image: FinishedImage) => void;
  onRerenderImage?: (image: FinishedImage) => void | Promise<void>;
  toggleSelectImage: (
    id: string,
    groupImages?: FinishedImage[],
    shiftKey?: boolean,
  ) => void;
}

interface MangaRowGroupProps {
  group: MangaRowGroupData;
  state: MangaRowGroupState;
  actions: MangaRowGroupActions;
}

export const MangaRowGroup: React.FC<MangaRowGroupProps> = ({
  group,
  state,
  actions,
}) => {
  const {
    isCollapsed,
    isDownloading,
    isRenaming,
    allInGroupSelected,
    readProgress,
    isRowHighlighted,
    renameInputValue,
    selectedImageIds,
    highlightedImageId,
    readerLoadingTitle,
    readerLoadError,
    summarizingTitle,
    reviewOnly,
    pageViewState,
  } = state;
  const {
    toggleGroupCollapse,
    setRenameInputValue,
    handleSaveRename,
    setRenamingManga,
    handleStartRename,
    setAssigningManga,
    handleToggleSelectAll,
    handleReadManga,
    handleSummarize,
    handleDownloadCbz,
    onDeleteManga,
    setConfirmDeleteManga,
    onMoveImage: handleCardMove,
    onReadFromHere: handleRowGroupReadFromHere,
    onClickImage: handleCardClick,
    onDownloadImage: handleCardDownload,
    onDeleteImage,
    handleCardDelete,
    onEditImage: handleCardEdit,
    onRerenderImage,
    toggleSelectImage,
  } = actions;

  return (
    <div
      data-manga-id={group.id}
      className={`rounded-2xl border ${
        isRowHighlighted
          ? "border-indigo-500 ring-4 ring-indigo-500/80 shadow-lg shadow-indigo-500/20"
          : "border-zinc-200 dark:border-zinc-800"
      } bg-white dark:bg-zinc-900/50 overflow-hidden shadow-xs transition-all duration-300`}
    >
      {/* Manga Group Header */}
      <div
        className={`flex flex-wrap items-center justify-between gap-3 p-4 bg-zinc-50/70 dark:bg-zinc-800/50 ${
          !isCollapsed ? "border-b border-zinc-200 dark:border-zinc-800" : ""
        }`}
      >
        <div className="flex items-center space-x-3">
          <button
            type="button"
            onClick={() => toggleGroupCollapse(group.title)}
            className="p-1 rounded text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 transition-colors cursor-pointer"
            title={isCollapsed ? "Expand Manga" : "Collapse Manga"}
          >
            <Icon
              icon={
                isCollapsed ? "carbon:chevron-right" : "carbon:chevron-down"
              }
              className="w-4 h-4"
            />
          </button>

          <div className="flex items-center space-x-2">
            {group.coverImage && (
              <button
                type="button"
                onClick={() => toggleGroupCollapse(group.title)}
                className="cursor-pointer focus:outline-hidden rounded-md transition-opacity hover:opacity-80 shrink-0"
                title={isCollapsed ? "Expand Manga" : "Collapse Manga"}
              >
                <MangaGroupThumbnail image={group.coverImage} />
              </button>
            )}

            {isRenaming ? (
              <div
                className="flex items-center space-x-1"
                onClick={(e) => e.stopPropagation()}
              >
                <input
                  type="text"
                  value={renameInputValue}
                  onChange={(e) => setRenameInputValue(e.target.value)}
                  maxLength={MANGA_TITLE_MAX_LENGTH}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleSaveRename(group.title);
                    if (e.key === "Escape") setRenamingManga(null);
                  }}
                  autoFocus
                  className="text-sm font-semibold rounded border border-indigo-400 dark:border-indigo-600 px-2 py-0.5 bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100"
                />
                <button
                  type="button"
                  onClick={() => handleSaveRename(group.title)}
                  className="p-1 text-emerald-600 hover:text-emerald-700 dark:text-emerald-400 cursor-pointer"
                  title="Save title"
                >
                  <Icon icon="carbon:checkmark" className="w-4 h-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setRenamingManga(null)}
                  className="p-1 text-zinc-400 hover:text-zinc-600 cursor-pointer"
                  title="Cancel"
                >
                  <Icon icon="carbon:close" className="w-4 h-4" />
                </button>
              </div>
            ) : (
              <div className="flex items-center space-x-1.5">
                <h4
                  className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 cursor-pointer hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors"
                  onClick={() => handleStartRename(group.title)}
                  title="Click to rename Manga"
                >
                  {group.title}
                </h4>
                <button
                  type="button"
                  onClick={() => handleStartRename(group.title)}
                  className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 p-0.5 cursor-pointer"
                  title="Rename Manga"
                >
                  <Icon icon="carbon:edit" className="w-3.5 h-3.5" />
                </button>
              </div>
            )}
          </div>

          <span className="rounded-full bg-zinc-200 dark:bg-zinc-700 px-2 py-0.5 text-[11px] font-medium text-zinc-700 dark:text-zinc-300">
            {group.count} {group.count === 1 ? "page" : "pages"}
          </span>
          {group.seriesTitle && (
            <button
              type="button"
              onClick={() =>
                setAssigningManga({
                  id: group.id,
                  title: group.title,
                  seriesId: group.seriesId,
                  seriesTitle: group.seriesTitle,
                })
              }
              className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2 py-0.5 text-[11px] font-medium text-indigo-700 hover:bg-indigo-200 dark:bg-indigo-950/60 dark:text-indigo-300 dark:hover:bg-indigo-900/80 transition-colors cursor-pointer"
              title={`In series: ${group.seriesTitle}. Click to change or move series`}
            >
              <Icon icon="carbon:catalog" className="w-3 h-3" />
              <span className="truncate max-w-28 sm:max-w-40">
                {group.seriesTitle}
              </span>
            </button>
          )}
          <MangaReadBadge progress={readProgress} pageCount={group.count} />
        </div>

        {/* Manga Actions: Detail, Read, Download CBZ & Select All */}
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Link
            to={buildMangaDetailIdUrl(group.id, reviewOnly)}
            state={pageViewState}
            className="flex items-center space-x-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-2.5 py-1 text-xs font-semibold text-zinc-700 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-700 transition-colors cursor-pointer"
            title={`Open ${group.title} manga detail page`}
          >
            <Icon icon="carbon:launch" className="w-3.5 h-3.5" />
            <span>Detail</span>
          </Link>

          <button
            type="button"
            onClick={() => handleToggleSelectAll(group.title, group.images)}
            className="flex items-center space-x-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-2.5 py-1 text-xs text-zinc-700 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-700 transition-colors cursor-pointer"
          >
            <Icon
              icon={
                allInGroupSelected
                  ? "carbon:checkbox-checked"
                  : "carbon:checkbox"
              }
              className="w-3.5 h-3.5"
            />
            <span>{allInGroupSelected ? "Deselect" : "Select All"}</span>
          </button>

          {!group.seriesId && (
            <button
              type="button"
              onClick={() =>
                setAssigningManga({
                  id: group.id,
                  title: group.title,
                  seriesId: null,
                  seriesTitle: null,
                })
              }
              className="flex items-center space-x-1 rounded-lg border border-indigo-200 dark:border-indigo-800 bg-white dark:bg-zinc-800 px-2.5 py-1 text-xs text-indigo-700 dark:text-indigo-300 hover:bg-indigo-50 dark:hover:bg-indigo-950/50 transition-colors cursor-pointer"
              title="Add to series"
            >
              <Icon icon="carbon:catalog" className="w-3.5 h-3.5" />
              <span>+ Series</span>
            </button>
          )}

          {/* Read Button — opens scroll reader */}
          <button
            type="button"
            onClick={() => handleReadManga(group.title, group.images)}
            disabled={Boolean(readerLoadingTitle)}
            className="flex items-center space-x-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-emerald-500 disabled:opacity-60 transition-colors cursor-pointer"
            title="Read manga in continuous scroll mode"
            aria-label={
              readerLoadError === group.title
                ? `Retry reading ${group.title}`
                : `Read ${group.title}`
            }
          >
            {readerLoadingTitle === group.title ? (
              <Icon icon="carbon:renew" className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Icon icon="carbon:book-open" className="w-3.5 h-3.5" />
            )}
            <span>
              {readerLoadingTitle === group.title
                ? "Loading…"
                : readerLoadError === group.title
                  ? "Retry Read"
                  : "Read"}
            </span>
          </button>

          <button
            type="button"
            onClick={() => void handleSummarize(group.title)}
            disabled={Boolean(summarizingTitle)}
            className="flex items-center space-x-1.5 rounded-lg border border-indigo-200 dark:border-indigo-800 bg-white dark:bg-zinc-800 px-3 py-1.5 text-xs font-semibold text-indigo-700 dark:text-indigo-300 hover:bg-indigo-50 dark:hover:bg-indigo-950/50 disabled:opacity-60 transition-colors cursor-pointer"
            title="Summarize the original text in this manga"
          >
            <Icon
              icon={
                summarizingTitle === group.title
                  ? "carbon:renew"
                  : "carbon:document"
              }
              className={`w-3.5 h-3.5 ${summarizingTitle === group.title ? "animate-spin" : ""}`}
            />
            <span>
              {summarizingTitle === group.title ? "Summarizing…" : "Summary"}
            </span>
          </button>

          {/* Primary Manga CBZ Export Button */}
          <button
            type="button"
            onClick={() => handleDownloadCbz(group.title, group.images)}
            disabled={isDownloading}
            className="flex items-center space-x-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 disabled:opacity-50 transition-colors cursor-pointer"
            title="Download the translated manga as a CBZ comic archive"
          >
            {isDownloading ? (
              <>
                <Icon
                  icon="carbon:renew"
                  className="w-3.5 h-3.5 animate-spin"
                />
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
            onClick={() => handleDownloadCbz(group.title, group.images, true)}
            disabled={isDownloading}
            className="flex items-center space-x-1.5 rounded-lg border border-zinc-300 bg-white px-3 py-1.5 text-xs font-semibold text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 transition-colors cursor-pointer dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
            title="Download the original manga pages as a CBZ comic archive"
          >
            <Icon icon="carbon:download" className="w-3.5 h-3.5" />
            <span>Original CBZ</span>
          </button>

          {/* Delete Manga */}
          {onDeleteManga && (
            <button
              type="button"
              onClick={() => setConfirmDeleteManga(group.title)}
              className="rounded-lg p-1.5 text-zinc-400 hover:text-red-500 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/50 transition-colors cursor-pointer"
              title="Delete this entire manga from library"
            >
              <Icon icon="carbon:trash-can" className="w-4 h-4" />
            </button>
          )}
        </div>

        {readerLoadError === group.title && (
          <div
            className="basis-full flex items-center justify-end gap-2 text-xs text-red-300"
            role="status"
          >
            <span>Couldn’t load all pages.</span>
            <button
              type="button"
              onClick={() => handleReadManga(group.title, group.images)}
              className="font-semibold text-red-200 underline underline-offset-2 hover:text-white"
            >
              Try again
            </button>
          </div>
        )}
      </div>

      {/* Group Pages Grid */}
      {!isCollapsed && (
        <div className="p-4">
          {group.isLoading ? (
            <div className="flex items-center justify-center py-12 text-zinc-400 space-x-2">
              <Icon
                icon="carbon:renew"
                className="w-5 h-5 animate-spin text-indigo-500"
              />
              <span className="text-sm font-medium">
                Loading pages for {group.title}...
              </span>
            </div>
          ) : group.images.length === 0 ? (
            <div className="text-center py-8 text-zinc-400 text-xs">
              No pages available for this manga.
            </div>
          ) : (
            <RowGroupCards
              title={group.title}
              images={group.images}
              highlightedImageId={highlightedImageId}
              selectedImageIds={selectedImageIds}
              onToggleSelectImage={toggleSelectImage}
              onMoveImage={handleCardMove}
              onReadFromHere={handleRowGroupReadFromHere}
              onClickImage={handleCardClick}
              onDownloadImage={handleCardDownload}
              onDeleteImage={onDeleteImage ? handleCardDelete : undefined}
              onEditImage={handleCardEdit}
              onRerenderImage={onRerenderImage}
              pageViewState={pageViewState}
            />
          )}
        </div>
      )}
    </div>
  );
};
