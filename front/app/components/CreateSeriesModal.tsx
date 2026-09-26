import React from "react";
import { Icon } from "@iconify/react";
import type { MangaGroupSummary } from "@/types";
import { MANGA_TITLE_MAX_LENGTH } from "@/config";

interface CreateSeriesModalProps {
  isCreateModalOpen: boolean;
  isCreating: boolean;
  createTitle: string;
  setCreateTitle: (title: string) => void;
  createSearch: string;
  setCreateSearch: (query: string) => void;
  unassignedGroupsForCreate: MangaGroupSummary[];
  createSelectedMap: Map<string, string>;
  toggleGroupForCreate: (id: string, title: string) => void;
  sortCreateManga: (mode: "natural" | "alpha-desc" | "reverse") => void;
  createDraggedId: string | null;
  setCreateDraggedId: (id: string | null) => void;
  createDragOverId: string | null;
  setCreateDragOverId: (id: string | null) => void;
  dropCreateManga: (sourceId: string, targetId: string) => void;
  moveCreateManga: (index: number, direction: -1 | 1) => void;
  createError: string | null;
  setIsCreateModalOpen: (open: boolean) => void;
  handleCreateNewSeries: () => void | Promise<void>;
}

const CreateSeriesModal: React.FC<CreateSeriesModalProps> = ({
  isCreateModalOpen,
  isCreating,
  createTitle,
  setCreateTitle,
  createSearch,
  setCreateSearch,
  unassignedGroupsForCreate,
  createSelectedMap,
  toggleGroupForCreate,
  sortCreateManga,
  createDraggedId,
  setCreateDraggedId,
  createDragOverId,
  setCreateDragOverId,
  dropCreateManga,
  moveCreateManga,
  createError,
  setIsCreateModalOpen,
  handleCreateNewSeries,
}) => isCreateModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-2 sm:p-4 backdrop-blur-xs" role="dialog" aria-modal="true" aria-labelledby="series-create-modal-title">
          <div className="w-full max-w-xl rounded-2xl sm:rounded-3xl bg-white p-3.5 sm:p-5 shadow-2xl dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 flex flex-col max-h-[92vh] sm:max-h-[90vh]">
            <div className="shrink-0">
              <h2 id="series-create-modal-title" className="text-base sm:text-lg font-semibold text-zinc-900 dark:text-zinc-100">Create new series</h2>
              <p className="mt-0.5 sm:mt-1 text-xs text-zinc-500 dark:text-zinc-400">Enter a series title, pick unassigned manga, and sort them into series order.</p>

              <label className="mt-3 sm:mt-4 block text-xs font-semibold text-zinc-600 dark:text-zinc-300">
                Series title
                <input
                  autoFocus
                  value={createTitle}
                  onChange={(event) => setCreateTitle(event.target.value)}
                  maxLength={MANGA_TITLE_MAX_LENGTH}
                  placeholder="e.g. My Favorite Manga"
                  className="mt-1 block w-full rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 focus:outline-hidden focus:ring-2 focus:ring-indigo-500/50"
                />
              </label>
            </div>

            <div className="mt-3 sm:mt-4 flex min-w-0 flex-1 flex-col overflow-y-auto space-y-3 sm:space-y-4 pr-1">
              {/* Step 1: Select Unassigned Manga */}
              <div>
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
                    1. Select unassigned manga ({createSelectedMap.size} selected)
                  </span>
                  <input
                    value={createSearch}
                    onChange={(e) => setCreateSearch(e.target.value)}
                    placeholder="Filter manga..."
                    className="rounded-lg border border-zinc-200 bg-white px-2.5 py-1 text-xs text-zinc-900 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100"
                  />
                </div>

                <div className="max-h-36 sm:max-h-40 overflow-y-auto rounded-xl border border-zinc-200 dark:border-zinc-800 p-1.5 sm:p-2 space-y-1 bg-zinc-50/50 dark:bg-zinc-950/40">
                  {unassignedGroupsForCreate.length === 0 ? (
                    <p className="py-4 text-center text-xs text-zinc-500">No unassigned manga found.</p>
                  ) : (
                    unassignedGroupsForCreate.map((group) => {
                      const isChecked = Boolean(group.id && createSelectedMap.has(group.id));
                      return (
                        <label
                          key={group.id}
                          className={`flex items-center justify-between gap-2 rounded-lg p-2 text-xs transition cursor-pointer ${
                            isChecked
                              ? 'bg-indigo-50 dark:bg-indigo-950/50 text-indigo-900 dark:text-indigo-200 font-medium'
                              : 'hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-800 dark:text-zinc-200'
                          }`}
                        >
                          <div className="flex items-center gap-2.5 min-w-0">
                            <input
                              type="checkbox"
                              checked={isChecked}
                              onChange={() => group.id && toggleGroupForCreate(group.id, group.title)}
                              className="h-4 w-4 rounded border-zinc-300 text-indigo-600 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-800"
                            />
                            <span className="break-words">{group.title}</span>
                          </div>
                          <span className="shrink-0 text-[11px] text-zinc-400">
                            {group.count} {group.count === 1 ? 'page' : 'pages'}
                          </span>
                        </label>
                      );
                    })
                  )}
                </div>
              </div>

              {/* Step 2: Order / Sort Selected Manga */}
              {createSelectedMap.size > 0 && (
                <div>
                  <div className="flex flex-wrap items-center justify-between gap-1.5 sm:gap-2 mb-2">
                    <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
                      2. Manga Order ({createSelectedMap.size})
                    </span>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => sortCreateManga('natural')}
                        className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-100 hover:text-indigo-600 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-indigo-400 border border-zinc-200 dark:border-zinc-700 cursor-pointer transition-colors touch-manipulation"
                        title="Sort naturally by title"
                      >
                        <Icon icon="carbon:sort-ascending" className="h-3.5 w-3.5" />
                        <span>A → Z</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => sortCreateManga('alpha-desc')}
                        className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-100 hover:text-indigo-600 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-indigo-400 border border-zinc-200 dark:border-zinc-700 cursor-pointer transition-colors touch-manipulation"
                        title="Sort reverse alphabetical"
                      >
                        <Icon icon="carbon:sort-descending" className="h-3.5 w-3.5" />
                        <span>Z → A</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => sortCreateManga('reverse')}
                        className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-100 hover:text-indigo-600 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-indigo-400 border border-zinc-200 dark:border-zinc-700 cursor-pointer transition-colors touch-manipulation"
                        title="Reverse order"
                      >
                        <Icon icon="carbon:arrows-vertical" className="h-3.5 w-3.5" />
                        <span>Reverse</span>
                      </button>
                    </div>
                  </div>

                  <div className="max-h-48 overflow-y-auto rounded-xl border border-zinc-200 bg-zinc-50/50 p-1.5 sm:p-2 space-y-1.5 dark:border-zinc-800 dark:bg-zinc-950/40 overscroll-contain">
                    {Array.from(createSelectedMap.entries()).map(([id, title], index, arr) => (
                      <div
                        key={id}
                        draggable={!isCreating}
                        onDragStart={(event) => {
                          setCreateDraggedId(id);
                          event.dataTransfer.effectAllowed = 'move';
                          event.dataTransfer.setData('text/plain', id);
                        }}
                        onDragOver={(event) => {
                          event.preventDefault();
                          event.dataTransfer.dropEffect = 'move';
                          setCreateDragOverId(id);
                        }}
                        onDrop={(event) => {
                          event.preventDefault();
                          dropCreateManga(event.dataTransfer.getData('text/plain') || createDraggedId || '', id);
                        }}
                        onDragEnd={() => {
                          setCreateDraggedId(null);
                          setCreateDragOverId(null);
                        }}
                        className={`flex items-center gap-2 sm:gap-2.5 rounded-xl border bg-white p-2 sm:p-2.5 transition-all dark:bg-zinc-900 ${
                          createDragOverId === id
                            ? 'border-indigo-500 ring-2 ring-indigo-200 dark:ring-indigo-900/60'
                            : 'border-zinc-200/80 dark:border-zinc-800 shadow-2xs'
                        } ${createDraggedId === id ? 'opacity-40' : ''}`}
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

                        <div className="min-w-0 flex-1">
                          <span className="block text-xs font-semibold text-zinc-900 dark:text-zinc-100 break-words leading-snug">
                            {title}
                          </span>
                        </div>

                        <div className="flex items-center gap-0.5 sm:gap-1 shrink-0">
                          <button
                            type="button"
                            onClick={() => moveCreateManga(index, -1)}
                            disabled={index === 0 || isCreating}
                            className="rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 disabled:opacity-30 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100 cursor-pointer disabled:cursor-not-allowed touch-manipulation min-w-[28px] min-h-[28px] flex items-center justify-center"
                            aria-label={`Move ${title} up`}
                            title="Move up"
                          >
                            <Icon icon="carbon:chevron-up" className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => moveCreateManga(index, 1)}
                            disabled={index === arr.length - 1 || isCreating}
                            className="rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 disabled:opacity-30 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100 cursor-pointer disabled:cursor-not-allowed touch-manipulation min-w-[28px] min-h-[28px] flex items-center justify-center"
                            aria-label={`Move ${title} down`}
                            title="Move down"
                          >
                            <Icon icon="carbon:chevron-down" className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => toggleGroupForCreate(id, title)}
                            disabled={isCreating}
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
              )}
            </div>

            {createError && <p role="alert" className="mt-3 text-xs text-red-600 dark:text-red-400 shrink-0">{createError}</p>}

            <div className="mt-5 flex justify-end gap-2 shrink-0 pt-2 border-t border-zinc-100 dark:border-zinc-800">
              <button
                type="button"
                onClick={() => setIsCreateModalOpen(false)}
                disabled={isCreating}
                className="rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleCreateNewSeries()}
                disabled={isCreating || createSelectedMap.size < 2 || !createTitle.trim()}
                className="rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50 transition-colors cursor-pointer"
              >
                {isCreating ? 'Creating…' : 'Create series'}
              </button>
            </div>
          </div>
        </div>
);

export default CreateSeriesModal;
