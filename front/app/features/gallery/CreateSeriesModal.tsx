import { Icon } from '@iconify/react';
import type { Dispatch, SetStateAction } from 'react';
import type { SeriesSummary } from '@/types';
import { MANGA_TITLE_MAX_LENGTH } from '@/config';
import { apiUrl } from '@/utils/api';

interface CreateSeriesModalProps {
  isCreateSeriesOpen: boolean;
  setIsCreateSeriesOpen: Dispatch<SetStateAction<boolean>>;
  seriesModalMode: 'create' | 'add';
  setSeriesModalMode: Dispatch<SetStateAction<'create' | 'add'>>;
  setSeriesError: Dispatch<SetStateAction<string | null>>;
  loadGalleryAllSeries: () => Promise<void>;
  newSeriesTitle: string;
  setNewSeriesTitle: Dispatch<SetStateAction<string>>;
  selectedMangaIds: Map<string, string>;
  isCreatingSeries: boolean;
  sortSelectedManga: (mode: 'natural' | 'alpha-desc' | 'reverse') => void;
  setCreateSeriesDraggedId: Dispatch<SetStateAction<string | null>>;
  createSeriesDraggedId: string | null;
  setCreateSeriesDragOverId: Dispatch<SetStateAction<string | null>>;
  createSeriesDragOverId: string | null;
  dropSelectedManga: (sourceId: string, targetId: string) => void;
  moveSelectedManga: (index: number, direction: -1 | 1) => void;
  toggleMangaSeriesSelection: (groupId: string) => void;
  existingSeriesSearch: string;
  setExistingSeriesSearch: Dispatch<SetStateAction<string>>;
  isLoadingExistingSeries: boolean;
  filteredExistingSeries: SeriesSummary[];
  targetExistingSeriesId: string | null;
  setTargetExistingSeriesId: Dispatch<SetStateAction<string | null>>;
  openSeriesAfterCreate: boolean;
  setOpenSeriesAfterCreate: Dispatch<SetStateAction<boolean>>;
  seriesError: string | null;
  handleCreateSeries: () => Promise<void>;
  handleAddToExistingSeries: () => Promise<void>;
}

export function CreateSeriesModal({
  isCreateSeriesOpen,
  setIsCreateSeriesOpen,
  seriesModalMode,
  setSeriesModalMode,
  setSeriesError,
  loadGalleryAllSeries,
  newSeriesTitle,
  setNewSeriesTitle,
  selectedMangaIds,
  isCreatingSeries,
  sortSelectedManga,
  setCreateSeriesDraggedId,
  createSeriesDraggedId,
  setCreateSeriesDragOverId,
  createSeriesDragOverId,
  dropSelectedManga,
  moveSelectedManga,
  toggleMangaSeriesSelection,
  existingSeriesSearch,
  setExistingSeriesSearch,
  isLoadingExistingSeries,
  filteredExistingSeries,
  targetExistingSeriesId,
  setTargetExistingSeriesId,
  openSeriesAfterCreate,
  setOpenSeriesAfterCreate,
  seriesError,
  handleCreateSeries,
  handleAddToExistingSeries,
}: CreateSeriesModalProps) {
  if (!isCreateSeriesOpen) return null;

  return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-2 sm:p-4 backdrop-blur-xs" role="dialog" aria-modal="true" aria-labelledby="series-modal-title">
          <div className="w-full max-w-xl rounded-2xl sm:rounded-3xl bg-white p-3.5 sm:p-5 shadow-2xl dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 flex flex-col max-h-[92vh] sm:max-h-[90vh]">
            <div className="shrink-0">
              <div className="flex items-center justify-between gap-2">
                <h2 id="series-modal-title" className="text-base sm:text-lg font-semibold text-zinc-900 dark:text-zinc-100">
                  {seriesModalMode === 'create' ? 'Create New Series' : 'Add to Existing Series'}
                </h2>
                <button
                  type="button"
                  onClick={() => setIsCreateSeriesOpen(false)}
                  className="rounded-lg p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
                  aria-label="Close dialog"
                >
                  <Icon icon="carbon:close" className="h-5 w-5" />
                </button>
              </div>

              {/* Mode Segmented Tab Switcher */}
              <div className="mt-2.5 flex rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800/80">
                <button
                  type="button"
                  onClick={() => {
                    setSeriesModalMode('create');
                    setSeriesError(null);
                  }}
                  className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg py-1.5 text-xs font-semibold transition-all cursor-pointer ${
                    seriesModalMode === 'create'
                      ? 'bg-white text-zinc-900 shadow-xs dark:bg-zinc-700 dark:text-zinc-100'
                      : 'text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-200'
                  }`}
                >
                  <Icon icon="carbon:folder-add" className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                  <span>Create New Series</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setSeriesModalMode('add');
                    setSeriesError(null);
                    void loadGalleryAllSeries();
                  }}
                  className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg py-1.5 text-xs font-semibold transition-all cursor-pointer ${
                    seriesModalMode === 'add'
                      ? 'bg-white text-zinc-900 shadow-xs dark:bg-zinc-700 dark:text-zinc-100'
                      : 'text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-200'
                  }`}
                >
                  <Icon icon="carbon:catalog" className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                  <span>Add to Existing Series</span>
                </button>
              </div>
            </div>

            {seriesModalMode === 'create' ? (
              <div className="flex min-w-0 flex-1 flex-col overflow-hidden pt-2">
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Arrange the manga in your preferred series order. Drag items or use the up/down buttons to reorder.
                </p>

                <label className="mt-3 block text-xs font-semibold text-zinc-600 dark:text-zinc-300">
                  Series title
                  <input
                    autoFocus
                    value={newSeriesTitle}
                    onChange={(event) => setNewSeriesTitle(event.target.value)}
                    maxLength={MANGA_TITLE_MAX_LENGTH}
                    onKeyDown={(event) => { if (event.key === 'Enter') void handleCreateSeries(); }}
                    placeholder="e.g. My Favorite Manga"
                    className="mt-1 block w-full rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 focus:outline-hidden focus:ring-2 focus:ring-indigo-500/50"
                  />
                </label>

                {selectedMangaIds.size < 2 && (
                  <div className="mt-2.5 rounded-xl border border-amber-200 bg-amber-50/80 p-2.5 text-xs text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
                    <p className="font-semibold">Need at least 2 manga to create a new series.</p>
                    <p className="mt-0.5 text-[11px] opacity-90">
                      Currently only {selectedMangaIds.size} manga is selected. You can switch to the <strong>“Add to Existing Series”</strong> tab to add this manga to an existing series, or select more manga in the gallery.
                    </p>
                  </div>
                )}

                {/* Ordered list of selected manga with full names and sorting */}
                <div className="mt-3 flex min-w-0 flex-1 flex-col overflow-hidden">
                  <div className="flex flex-wrap items-center justify-between gap-1.5 sm:gap-2 mb-2 shrink-0">
                    <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
                      Manga Order ({selectedMangaIds.size})
                    </span>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => sortSelectedManga('natural')}
                        className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-100 hover:text-indigo-600 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-indigo-400 border border-zinc-200 dark:border-zinc-700 cursor-pointer transition-colors touch-manipulation"
                        title="Sort naturally by title (e.g. Vol 1 before Vol 2)"
                      >
                        <Icon icon="carbon:sort-ascending" className="h-3.5 w-3.5" />
                        <span>A → Z</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => sortSelectedManga('alpha-desc')}
                        className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-100 hover:text-indigo-600 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-indigo-400 border border-zinc-200 dark:border-zinc-700 cursor-pointer transition-colors touch-manipulation"
                        title="Sort in reverse alphabetical order"
                      >
                        <Icon icon="carbon:sort-descending" className="h-3.5 w-3.5" />
                        <span>Z → A</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => sortSelectedManga('reverse')}
                        className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-100 hover:text-indigo-600 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-indigo-400 border border-zinc-200 dark:border-zinc-700 cursor-pointer transition-colors touch-manipulation"
                        title="Reverse current order"
                      >
                        <Icon icon="carbon:arrows-vertical" className="h-3.5 w-3.5" />
                        <span>Reverse</span>
                      </button>
                    </div>
                  </div>

                  <div className="flex-1 overflow-y-auto max-h-56 sm:max-h-60 rounded-xl border border-zinc-200 bg-zinc-50/50 p-1.5 sm:p-2 space-y-1.5 dark:border-zinc-800 dark:bg-zinc-950/40 overscroll-contain">
                    {Array.from(selectedMangaIds.entries()).map(([id, title], index, arr) => (
                      <div
                        key={id}
                        draggable={!isCreatingSeries}
                        onDragStart={(event) => {
                          setCreateSeriesDraggedId(id);
                          event.dataTransfer.effectAllowed = 'move';
                          event.dataTransfer.setData('text/plain', id);
                        }}
                        onDragOver={(event) => {
                          event.preventDefault();
                          event.dataTransfer.dropEffect = 'move';
                          setCreateSeriesDragOverId(id);
                        }}
                        onDrop={(event) => {
                          event.preventDefault();
                          dropSelectedManga(event.dataTransfer.getData('text/plain') || createSeriesDraggedId || '', id);
                        }}
                        onDragEnd={() => {
                          setCreateSeriesDraggedId(null);
                          setCreateSeriesDragOverId(null);
                        }}
                        className={`flex items-center gap-2 sm:gap-2.5 rounded-xl border bg-white p-2 sm:p-2.5 transition-all dark:bg-zinc-900 ${
                          createSeriesDragOverId === id
                            ? 'border-indigo-500 ring-2 ring-indigo-200 dark:ring-indigo-900/60'
                            : 'border-zinc-200/80 dark:border-zinc-800 shadow-2xs'
                        } ${createSeriesDraggedId === id ? 'opacity-40' : ''}`}
                      >
                        <span
                          className="cursor-grab select-none text-base leading-none text-zinc-400 active:cursor-grabbing hover:text-zinc-600 dark:hover:text-zinc-200 shrink-0 touch-none"
                          title="Drag to reorder"
                          aria-hidden="true"
                        >
                          ⋮⋮
                        </span>
                        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-zinc-100 text-[11px] font-bold text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                          {index + 1}
                        </span>

                        {/* FULL NAME DISPLAY (Unclipped, wraps naturally) */}
                        <div className="min-w-0 flex-1">
                          <span className="block text-xs font-semibold text-zinc-900 dark:text-zinc-100 break-words leading-snug">
                            {title}
                          </span>
                        </div>

                        <div className="flex items-center gap-0.5 sm:gap-1 shrink-0">
                          <button
                            type="button"
                            onClick={() => moveSelectedManga(index, -1)}
                            disabled={index === 0 || isCreatingSeries}
                            className="rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 disabled:opacity-30 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100 cursor-pointer disabled:cursor-not-allowed touch-manipulation min-w-[28px] min-h-[28px] flex items-center justify-center"
                            aria-label={`Move ${title} up`}
                            title="Move up"
                          >
                            <Icon icon="carbon:chevron-up" className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => moveSelectedManga(index, 1)}
                            disabled={index === arr.length - 1 || isCreatingSeries}
                            className="rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 disabled:opacity-30 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100 cursor-pointer disabled:cursor-not-allowed touch-manipulation min-w-[28px] min-h-[28px] flex items-center justify-center"
                            aria-label={`Move ${title} down`}
                            title="Move down"
                          >
                            <Icon icon="carbon:chevron-down" className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => toggleMangaSeriesSelection(id)}
                            disabled={isCreatingSeries}
                            className="rounded-lg p-1.5 text-zinc-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40 dark:hover:text-red-400 cursor-pointer touch-manipulation min-w-[28px] min-h-[28px] flex items-center justify-center"
                            title={`Remove ${title}`}
                          >
                            <Icon icon="carbon:close" className="h-4 w-4" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex min-w-0 flex-1 flex-col overflow-hidden pt-2">
                {/* Manga Selected Preview Chips */}
                <div className="mb-3 shrink-0">
                  <div className="flex items-center justify-between text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
                    <span>Selected manga to add ({selectedMangaIds.size})</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5 max-h-16 overflow-y-auto p-1.5 rounded-lg bg-zinc-50 border border-zinc-200/70 dark:bg-zinc-800/50 dark:border-zinc-700/60">
                    {Array.from(selectedMangaIds.entries()).map(([id, title]) => (
                      <span
                        key={id}
                        className="inline-flex items-center gap-1 rounded-md bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300 max-w-full truncate border border-indigo-200/50 dark:border-indigo-800/40"
                        title={title}
                      >
                        <span className="truncate">{title}</span>
                        <button
                          type="button"
                          onClick={() => toggleMangaSeriesSelection(id)}
                          className="text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-200 ml-0.5 cursor-pointer"
                          title={`Remove ${title}`}
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                </div>

                {/* Search Input */}
                <div className="relative shrink-0 mb-2.5">
                  <Icon
                    icon="carbon:search"
                    className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400 h-4 w-4"
                  />
                  <input
                    autoFocus
                    type="text"
                    value={existingSeriesSearch}
                    onChange={(e) => setExistingSeriesSearch(e.target.value)}
                    placeholder="Search series by name..."
                    className="w-full rounded-xl border border-zinc-200 bg-zinc-50 py-2 pl-9 pr-8 text-xs text-zinc-900 focus:border-indigo-500 focus:bg-white focus:outline-hidden dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 dark:focus:bg-zinc-900"
                  />
                  {existingSeriesSearch && (
                    <button
                      type="button"
                      onClick={() => setExistingSeriesSearch('')}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 cursor-pointer"
                    >
                      <Icon icon="carbon:close-filled" className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>

                {/* Scrollable Series Selection List */}
                <div className="flex-1 overflow-y-auto max-h-56 sm:max-h-60 rounded-xl border border-zinc-200 bg-zinc-50/50 p-1.5 sm:p-2 space-y-1.5 dark:border-zinc-800 dark:bg-zinc-950/40 overscroll-contain">
                  {isLoadingExistingSeries ? (
                    <div className="py-8 text-center text-xs text-zinc-500">
                      <Icon icon="carbon:renew" className="mx-auto mb-2 h-5 w-5 animate-spin text-indigo-500" />
                      Loading series...
                    </div>
                  ) : filteredExistingSeries.length === 0 ? (
                    <div className="py-8 text-center text-xs text-zinc-500">
                      <Icon icon="carbon:catalog" className="mx-auto mb-2 h-7 w-7 text-zinc-400 opacity-60" />
                      <p className="font-medium text-zinc-700 dark:text-zinc-300">
                        {existingSeriesSearch ? 'No matching series found' : 'No existing series found'}
                      </p>
                      <p className="mt-1 text-zinc-400">Switch to the “Create New Series” tab to make a new series.</p>
                    </div>
                  ) : (
                    filteredExistingSeries.map((s) => {
                      const isSelected = targetExistingSeriesId === s.id;
                      const coverVal = s.cover?.coverUrl || s.cover?.thumbnailUrl || (typeof s.cover?.result === 'string' ? s.cover.result : null);
                      const thumb = coverVal ? apiUrl(coverVal) : null;
                      return (
                        <label
                          key={s.id}
                          onClick={() => setTargetExistingSeriesId(s.id)}
                          className={`flex items-center justify-between gap-3 rounded-xl border p-2.5 transition-all cursor-pointer ${
                            isSelected
                              ? 'border-indigo-500 ring-2 ring-indigo-200 bg-indigo-50/50 dark:border-indigo-500 dark:ring-indigo-900/60 dark:bg-indigo-950/30 shadow-2xs'
                              : 'border-zinc-200 bg-white hover:border-indigo-300 hover:bg-zinc-50/80 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-indigo-800 dark:hover:bg-zinc-800/80'
                          }`}
                        >
                          <div className="flex items-center gap-2.5 min-w-0 flex-1">
                            <input
                              type="radio"
                              name="targetExistingSeries"
                              checked={isSelected}
                              onChange={() => setTargetExistingSeriesId(s.id)}
                              className="h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-zinc-300 dark:border-zinc-700 shrink-0"
                            />
                            {thumb ? (
                              <img
                                src={thumb}
                                alt=""
                                className="h-10 w-8 shrink-0 rounded-md object-cover border border-zinc-200/60 dark:border-zinc-800"
                              />
                            ) : (
                              <div className="flex h-10 w-8 shrink-0 items-center justify-center rounded-md bg-zinc-100 text-zinc-400 dark:bg-zinc-800">
                                <Icon icon="carbon:book" className="h-3.5 w-3.5" />
                              </div>
                            )}
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                                {s.title}
                              </p>
                              <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                                {s.memberCount} {s.memberCount === 1 ? 'manga' : 'manga'}
                              </p>
                            </div>
                          </div>
                        </label>
                      );
                    })
                  )}
                </div>
              </div>
            )}

            <div className="shrink-0 pt-3">
              {/* Checkbox for direct navigation option */}
              <label className="flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={openSeriesAfterCreate}
                  onChange={(e) => setOpenSeriesAfterCreate(e.target.checked)}
                  className="h-4 w-4 rounded border-zinc-300 text-indigo-600 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-800"
                />
                <span>Open series detail view immediately</span>
              </label>

              {seriesError && <p role="alert" className="mt-2.5 text-xs text-red-600 dark:text-red-400">{seriesError}</p>}
              <div className="mt-4 sm:mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setIsCreateSeriesOpen(false)}
                  disabled={isCreatingSeries}
                  className="flex-1 sm:flex-initial rounded-xl border border-zinc-200 px-3.5 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 transition-colors cursor-pointer touch-manipulation text-center"
                >
                  Cancel
                </button>
                {seriesModalMode === 'create' ? (
                  <button
                    type="button"
                    onClick={() => void handleCreateSeries()}
                    disabled={isCreatingSeries || selectedMangaIds.size < 2 || !newSeriesTitle.trim()}
                    className="flex-1 sm:flex-initial rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50 transition-colors cursor-pointer touch-manipulation text-center"
                  >
                    {isCreatingSeries ? 'Creating…' : 'Create series'}
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void handleAddToExistingSeries()}
                    disabled={isCreatingSeries || selectedMangaIds.size === 0 || !targetExistingSeriesId}
                    className="flex-1 sm:flex-initial rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50 transition-colors cursor-pointer touch-manipulation text-center"
                  >
                    {isCreatingSeries ? 'Adding…' : 'Add to series'}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
  );
}
