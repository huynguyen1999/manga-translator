import React, { useEffect, useMemo, useState } from "react";
import { Icon } from "@iconify/react";
import type { MangaGroupSummary, SeriesSummary } from "@/types";
import { apiUrl } from "@/utils/api";
import { MANGA_TITLE_MAX_LENGTH } from "@/config";
import {
  createSeries,
  fetchAllSeries,
  moveMangaToSeries,
  removeMangaFromSeries,
} from "@/utils/series";

interface AssignToSeriesModalProps {
  isOpen: boolean;
  onClose: () => void;
  mangaId: string;
  mangaTitle: string;
  currentSeriesId?: string | null;
  currentSeriesTitle?: string | null;
  onSeriesAssigned?: (seriesId: string) => void | Promise<void>;
  onSeriesRemoved?: () => void | Promise<void>;
}

const coverUrl = (cover: any) => {
  const value = cover?.coverUrl || cover?.thumbnailUrl || cover?.resultUrl || cover?.result;
  return value ? apiUrl(value) : null;
};

export const AssignToSeriesModal: React.FC<AssignToSeriesModalProps> = ({
  isOpen,
  onClose,
  mangaId,
  mangaTitle,
  currentSeriesId,
  currentSeriesTitle,
  onSeriesAssigned,
  onSeriesRemoved,
}) => {
  const [search, setSearch] = useState("");
  const [allSeries, setAllSeries] = useState<SeriesSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // New series creation mode
  const [isCreatingNew, setIsCreatingNew] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [unassignedGroups, setUnassignedGroups] = useState<MangaGroupSummary[]>([]);
  const [selectedPairId, setSelectedPairId] = useState<string | null>(null);
  const [loadingUnassigned, setLoadingUnassigned] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      setSearch("");
      setError(null);
      setIsCreatingNew(false);
      setNewTitle("");
      setSelectedPairId(null);
      return;
    }

    let cancelled = false;
    setIsLoading(true);
    setError(null);

    fetchAllSeries()
      .then((list) => {
        if (!cancelled) setAllSeries(list);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load series.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isSubmitting) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, isSubmitting, onClose]);

  const loadUnassignedForNewSeries = async () => {
    setLoadingUnassigned(true);
    try {
      const response = await fetch(apiUrl("/api/results/groups?limit=500"));
      if (response.ok) {
        const data = await response.json();
        if (Array.isArray(data.groups)) {
          const list: MangaGroupSummary[] = data.groups
            .filter((g: any) => !g.seriesId && String(g.id) !== mangaId)
            .map((g: any) => ({
              id: g.id,
              title: String(g.title || "Untitled"),
              count: Number(g.count || 0),
            }));
          setUnassignedGroups(list);
          if (list.length > 0 && !selectedPairId) {
            setSelectedPairId(list[0].id || null);
          }
        }
      }
    } catch {
      // ignore
    } finally {
      setLoadingUnassigned(false);
    }
  };

  const filteredSeries = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return allSeries;
    return allSeries.filter((s) => s.title.toLowerCase().includes(q));
  }, [allSeries, search]);

  const handleSelectSeries = async (targetSeriesId: string) => {
    if (isSubmitting || targetSeriesId === currentSeriesId) return;
    setIsSubmitting(true);
    setError(null);
    try {
      await moveMangaToSeries(mangaId, targetSeriesId);
      await onSeriesAssigned?.(targetSeriesId);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to move manga to series.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRemoveFromSeries = async () => {
    if (isSubmitting || !currentSeriesId) return;
    if (!window.confirm(`Remove “${mangaTitle}” from series “${currentSeriesTitle || "this series"}”?`)) return;
    setIsSubmitting(true);
    setError(null);
    try {
      await removeMangaFromSeries(mangaId);
      await onSeriesRemoved?.();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove manga from series.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCreateNewSeries = async () => {
    const title = newTitle.trim();
    if (!title) {
      setError("Please enter a series title.");
      return;
    }
    if (!selectedPairId) {
      setError("Please pick at least one other manga to pair into this series.");
      return;
    }
    if (isSubmitting) return;
    setIsSubmitting(true);
    setError(null);
    try {
      const created = await createSeries(title, [mangaId, selectedPairId]);
      await onSeriesAssigned?.(created.id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create series.");
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-2 sm:p-4 backdrop-blur-xs"
      role="dialog"
      aria-modal="true"
      aria-labelledby="assign-series-title"
    >
      <div className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-2xl border border-zinc-200 bg-white p-4 shadow-2xl dark:border-zinc-800 dark:bg-zinc-900 sm:p-5">
        {/* Header */}
        <div className="flex items-start justify-between gap-2 shrink-0 pb-3 border-b border-zinc-100 dark:border-zinc-800">
          <div className="min-w-0 flex-1">
            <h2 id="assign-series-title" className="text-base font-bold text-zinc-900 dark:text-zinc-100 truncate">
              {currentSeriesId ? "Move to Series" : "Add to Series"}
            </h2>
            <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400 truncate" title={mangaTitle}>
              Manga: <strong className="text-zinc-700 dark:text-zinc-200">{mangaTitle}</strong>
            </p>
            {currentSeriesTitle && (
              <p className="mt-0.5 text-[11px] text-indigo-600 dark:text-indigo-400">
                Current series: {currentSeriesTitle}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
            aria-label="Close dialog"
          >
            <Icon icon="carbon:close" className="h-5 w-5" />
          </button>
        </div>

        {/* Content Body */}
        {!isCreatingNew ? (
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden pt-3">
            {/* Search Bar */}
            <div className="relative shrink-0 mb-3">
              <Icon
                icon="carbon:search"
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400 h-4 w-4"
              />
              <input
                autoFocus
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search series by name..."
                className="w-full rounded-xl border border-zinc-200 bg-zinc-50 py-2 pl-9 pr-8 text-xs text-zinc-900 focus:border-indigo-500 focus:bg-white focus:outline-hidden dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 dark:focus:bg-zinc-900"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => setSearch("")}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
                >
                  <Icon icon="carbon:close-filled" className="h-3.5 w-3.5" />
                </button>
              )}
            </div>

            {/* Series List */}
            <div className="flex-1 overflow-y-auto space-y-2 pr-0.5 max-h-72">
              {isLoading ? (
                <div className="py-8 text-center text-xs text-zinc-500">
                  <Icon icon="carbon:renew" className="mx-auto mb-2 h-5 w-5 animate-spin text-indigo-500" />
                  Loading series...
                </div>
              ) : filteredSeries.length === 0 ? (
                <div className="py-8 text-center text-xs text-zinc-500">
                  <Icon icon="carbon:catalog" className="mx-auto mb-2 h-7 w-7 text-zinc-400 opacity-60" />
                  <p className="font-medium text-zinc-700 dark:text-zinc-300">
                    {search ? "No matching series found" : "No series available"}
                  </p>
                  <p className="mt-1 text-zinc-400">Create a new series to get started.</p>
                </div>
              ) : (
                filteredSeries.map((s) => {
                  const isCurrent = s.id === currentSeriesId;
                  const thumb = coverUrl(s.cover);
                  return (
                    <div
                      key={s.id}
                      className={`flex items-center justify-between gap-3 rounded-xl border p-2.5 transition-all ${
                        isCurrent
                          ? "border-indigo-200 bg-indigo-50/50 dark:border-indigo-900/50 dark:bg-indigo-950/20"
                          : "border-zinc-200 bg-white hover:border-indigo-300 hover:bg-zinc-50/60 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-indigo-800 dark:hover:bg-zinc-800/60"
                      }`}
                    >
                      <div className="flex items-center gap-3 min-w-0 flex-1">
                        {thumb ? (
                          <img
                            src={thumb}
                            alt=""
                            className="h-12 w-9 shrink-0 rounded-md object-cover border border-zinc-200/60 dark:border-zinc-800"
                          />
                        ) : (
                          <div className="flex h-12 w-9 shrink-0 items-center justify-center rounded-md bg-zinc-100 text-zinc-400 dark:bg-zinc-800">
                            <Icon icon="carbon:book" className="h-4 w-4" />
                          </div>
                        )}
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                            {s.title}
                          </p>
                          <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                            {s.memberCount} {s.memberCount === 1 ? "manga" : "manga"}
                          </p>
                        </div>
                      </div>

                      <div className="shrink-0">
                        {isCurrent ? (
                          <span className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2 py-0.5 text-[10px] font-semibold text-indigo-700 dark:bg-indigo-900/60 dark:text-indigo-300">
                            <Icon icon="carbon:checkmark" className="h-3 w-3" />
                            Current
                          </span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => void handleSelectSeries(s.id)}
                            disabled={isSubmitting}
                            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-2xs hover:bg-indigo-500 disabled:opacity-50 cursor-pointer transition-colors"
                          >
                            {isSubmitting ? "Moving..." : "Move Here"}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            {error && (
              <p role="alert" className="mt-2 text-xs text-red-600 dark:text-red-400 shrink-0">
                {error}
              </p>
            )}

            {/* Bottom Actions */}
            <div className="mt-4 flex flex-wrap items-center justify-between gap-2 shrink-0 pt-3 border-t border-zinc-100 dark:border-zinc-800">
              {currentSeriesId ? (
                <button
                  type="button"
                  onClick={() => void handleRemoveFromSeries()}
                  disabled={isSubmitting}
                  className="inline-flex items-center gap-1 text-xs font-medium text-red-600 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 cursor-pointer"
                >
                  <Icon icon="carbon:trash-can" className="h-3.5 w-3.5" />
                  <span>Remove from series</span>
                </button>
              ) : (
                <div />
              )}

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setIsCreatingNew(true);
                    setNewTitle(mangaTitle);
                    void loadUnassignedForNewSeries();
                  }}
                  className="inline-flex items-center gap-1 rounded-lg border border-zinc-200 px-3 py-1.5 text-xs font-semibold text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 cursor-pointer transition-colors"
                >
                  <Icon icon="carbon:add" className="h-3.5 w-3.5" />
                  <span>+ Create new series</span>
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-lg border border-zinc-200 px-3 py-1.5 text-xs font-semibold text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 cursor-pointer transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        ) : (
          /* Create New Series Panel */
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden pt-3 space-y-3">
            <div>
              <label className="block text-xs font-semibold text-zinc-700 dark:text-zinc-300">
                New Series Title
                <input
                  autoFocus
                  type="text"
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  maxLength={MANGA_TITLE_MAX_LENGTH}
                  placeholder="e.g. My Series Title"
                  className="mt-1 block w-full rounded-xl border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-900 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100"
                />
              </label>
            </div>

            <div className="flex-1 overflow-hidden flex flex-col">
              <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1">
                Select another manga to pair (series requires 2+ manga):
              </span>
              <div className="flex-1 overflow-y-auto max-h-48 rounded-xl border border-zinc-200 p-2 space-y-1 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-950/40">
                {loadingUnassigned ? (
                  <p className="py-4 text-center text-xs text-zinc-500">Loading manga...</p>
                ) : unassignedGroups.length === 0 ? (
                  <p className="py-4 text-center text-xs text-zinc-500">
                    No unassigned manga found to pair.
                  </p>
                ) : (
                  unassignedGroups.map((g) => {
                    const isSelected = selectedPairId === g.id;
                    return (
                      <label
                        key={g.id}
                        className={`flex items-center justify-between gap-2 rounded-lg p-2 text-xs cursor-pointer transition-colors ${
                          isSelected
                            ? "bg-indigo-50 text-indigo-900 font-medium dark:bg-indigo-950/60 dark:text-indigo-200"
                            : "hover:bg-zinc-100 text-zinc-800 dark:hover:bg-zinc-800 dark:text-zinc-200"
                        }`}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <input
                            type="radio"
                            name="pairManga"
                            checked={isSelected}
                            onChange={() => setSelectedPairId(g.id || null)}
                            className="h-3.5 w-3.5 text-indigo-600"
                          />
                          <span className="truncate">{g.title}</span>
                        </div>
                        <span className="shrink-0 text-[10px] text-zinc-400">
                          {g.count} {g.count === 1 ? "page" : "pages"}
                        </span>
                      </label>
                    );
                  })
                )}
              </div>
            </div>

            {error && (
              <p role="alert" className="text-xs text-red-600 dark:text-red-400 shrink-0">
                {error}
              </p>
            )}

            <div className="mt-3 flex justify-end gap-2 shrink-0 pt-2 border-t border-zinc-100 dark:border-zinc-800">
              <button
                type="button"
                onClick={() => setIsCreatingNew(false)}
                className="rounded-lg border border-zinc-200 px-3 py-1.5 text-xs font-semibold text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 cursor-pointer"
              >
                Back to search
              </button>
              <button
                type="button"
                onClick={() => void handleCreateNewSeries()}
                disabled={isSubmitting || !newTitle.trim() || !selectedPairId}
                className="rounded-lg bg-indigo-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50 cursor-pointer transition-colors"
              >
                {isSubmitting ? "Creating..." : "Create Series"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
