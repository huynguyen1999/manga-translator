import React, { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "@iconify/react";
import { Pagination } from "./Pagination";
import { AssignToSeriesModal } from "./AssignToSeriesModal";
import CreateSeriesModal from "./CreateSeriesModal";
import type { MangaGroupSummary, SeriesDetail, SeriesMember, SeriesSummary } from "@/types";
import { apiUrl } from "@/utils/api";
import { MANGA_TITLE_MAX_LENGTH } from "@/config";
import {
  addMangaToSeries,
  createSeries,
  deleteSeries,
  fetchSeries,
  fetchSeriesDetail,
  renameSeries,
  reorderSeriesMembers,
  replaceSeriesMembers,
} from "@/utils/series";
import { naturalCompare } from "@/utils/zipUtils";

interface SeriesLibraryProps {
  seriesId?: string | null;
  onOpenSeriesDetail: (seriesId: string) => void;
  onCloseSeriesDetail: () => void;
  onOpenMangaDetail?: (mangaId: string) => void;
  onOpenReader?: (mangaId: string) => void;
  onSeriesChanged?: () => void | Promise<void>;
}

type CoverImage = { coverUrl?: string | null; thumbnailUrl?: string | null; resultUrl?: string | null; result?: string | null };

const imageUrl = (image?: CoverImage | null) => {
  const value = image?.coverUrl || image?.thumbnailUrl || image?.resultUrl || image?.result;
  return value ? apiUrl(value) : null;
};

const coverUrl = (image?: CoverImage | null) => imageUrl(image);

const mapGroup = (value: any): MangaGroupSummary => ({
  id: value.id,
  title: String(value.title || "Untitled"),
  count: Number(value.count || 0),
  cover: value.cover || null,
  latestFinishedAt: value.latestFinishedAt,
  seriesId: value.seriesId || null,
  seriesTitle: value.seriesTitle || null,
  hasSummary: Boolean(value.hasSummary),
});

const loadAllGroups = async (): Promise<MangaGroupSummary[]> => {
  const groups: MangaGroupSummary[] = [];
  let offset = 0;
  while (true) {
    const response = await fetch(apiUrl(`/api/results/groups?limit=500&offset=${offset}`));
    if (!response.ok) throw new Error(`Loading manga groups failed (${response.status})`);
    const data = await response.json();
    if (!Array.isArray(data.groups)) break;
    groups.push(...data.groups.map(mapGroup));
    if (data.nextOffset === null || data.nextOffset === undefined) break;
    offset = Number(data.nextOffset);
  }
  return groups;
};

const Button: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement>> = ({ className = "", ...props }) => (
  <button
    {...props}
    className={`inline-flex min-h-9 items-center justify-center rounded-lg border border-zinc-200 px-3 py-1.5 text-xs font-semibold text-zinc-700 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800 cursor-pointer ${className}`}
  />
);

const SeriesCover: React.FC<{ series: SeriesSummary | SeriesDetail; className?: string }> = ({ series, className = "" }) => {
  const src = coverUrl(series.cover as any);
  return src ? (
    <img src={src} alt="" className={`h-24 w-16 shrink-0 rounded-lg object-cover shadow-2xs ${className}`} />
  ) : (
    <div className={`flex h-24 w-16 shrink-0 items-center justify-center rounded-lg bg-zinc-100 text-zinc-400 dark:bg-zinc-800 ${className}`}>
      <Icon icon="carbon:book" className="h-7 w-7" />
    </div>
  );
};

const MemberCover: React.FC<{ member: SeriesMember; className?: string }> = ({ member, className = "" }) => {
  const src = coverUrl(member.cover as any);
  return src ? (
    <img src={src} alt="" className={`h-20 w-14 shrink-0 rounded-lg object-cover shadow-2xs ${className}`} />
  ) : (
    <div className={`flex h-20 w-14 shrink-0 items-center justify-center rounded-lg bg-zinc-100 text-zinc-400 dark:bg-zinc-800 ${className}`}>
      <Icon icon="carbon:book" className="h-5 w-5" />
    </div>
  );
};

export const SeriesLibrary: React.FC<SeriesLibraryProps> = ({
  seriesId,
  onOpenSeriesDetail,
  onCloseSeriesDetail,
  onOpenMangaDetail,
  onOpenReader,
  onSeriesChanged,
}) => {
  const [series, setSeries] = useState<SeriesSummary[]>([]);
  const [detail, setDetail] = useState<SeriesDetail | null>(null);
  const [search, setSearch] = useState("");
  const [seriesSearch, setSeriesSearch] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [saving, setSaving] = useState(false);

  // Add manga modal state
  const [groups, setGroups] = useState<MangaGroupSummary[]>([]);
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [addSearch, setAddSearch] = useState("");
  const [selectedGroupIds, setSelectedGroupIds] = useState<Set<string>>(new Set());

  // Move manga to another series modal state
  const [movingMember, setMovingMember] = useState<{ id: string; title: string } | null>(null);

  // Create series modal state
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [createTitle, setCreateTitle] = useState("");
  const [createSearch, setCreateSearch] = useState("");
  const [createSelectedMap, setCreateSelectedMap] = useState<Map<string, string>>(new Map());
  const [createDraggedId, setCreateDraggedId] = useState<string | null>(null);
  const [createDragOverId, setCreateDragOverId] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);

  useEffect(() => {
    if (!isAddOpen && !isCreateModalOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || isCreating) return;
      if (isAddOpen) setIsAddOpen(false);
      else setIsCreateModalOpen(false);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isAddOpen, isCreateModalOpen, isCreating]);

  const [draggedMemberId, setDraggedMemberId] = useState<string | null>(null);
  const [dragOverMemberId, setDragOverMemberId] = useState<string | null>(null);
  const seriesPageCacheRef = useRef(new Map<string, { series: SeriesSummary[]; totalSeries: number }>());
  const pageSize = 24;

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setSeriesSearch(search);
      setPage(1);
    }, 250);
    return () => window.clearTimeout(timeout);
  }, [search]);

  useEffect(() => {
    if (seriesId) return;
    let cancelled = false;
    setError(null);
    const cacheKey = `${page}:${seriesSearch.trim().toLocaleLowerCase()}`;
    const cached = seriesPageCacheRef.current.get(cacheKey);
    if (cached) {
      setSeries(cached.series);
      setTotal(cached.totalSeries);
      setLoading(false);
      return () => { cancelled = true; };
    }
    setLoading(series.length === 0);
    fetchSeries(pageSize, (page - 1) * pageSize, seriesSearch)
      .then((result) => {
        if (cancelled) return;
        setSeries(result.series);
        setTotal(result.totalSeries);
        seriesPageCacheRef.current.set(cacheKey, { series: result.series, totalSeries: result.totalSeries });
      })
      .catch((reason) => !cancelled && setError(reason instanceof Error ? reason.message : "Could not load series."))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [page, seriesId, seriesSearch, series.length]);

  const loadDetail = async (id: string) => {
    try {
      const value = await fetchSeriesDetail(id);
      setDetail(value);
      setEditingTitle(value.title);
      setSeries((previous) => previous.some((entry) => entry.id === value.id)
        ? previous.map((entry) => entry.id === value.id ? value : entry)
        : [value, ...previous]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load series.");
    }
  };

  useEffect(() => {
    if (!seriesId) {
      setDetail(null);
      return;
    }
    seriesPageCacheRef.current.clear();
    let cancelled = false;
    setError(null);
    setLoading(true);
    fetchSeriesDetail(seriesId)
      .then((value) => {
        if (!cancelled) {
          setDetail(value);
          setEditingTitle(value.title);
          setSeries((previous) => previous.some((entry) => entry.id === value.id)
            ? previous.map((entry) => entry.id === value.id ? value : entry)
            : [value, ...previous]);
        }
      })
      .catch((reason) => !cancelled && setError(reason instanceof Error ? reason.message : "Could not load series."))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [seriesId]);

  const unassignedGroupsForAdd = useMemo(() => {
    const query = addSearch.trim().toLowerCase();
    const currentMemberIds = new Set(detail?.members.map((m) => m.id) || []);
    return groups.filter((group) => !currentMemberIds.has(group.id || "") && (!query || group.title.toLowerCase().includes(query)));
  }, [groups, addSearch, detail]);

  const unassignedGroupsForCreate = useMemo(() => {
    const query = createSearch.trim().toLowerCase();
    return groups.filter((group) => !group.seriesId && (!query || group.title.toLowerCase().includes(query)));
  }, [groups, createSearch]);

  const refreshChanged = async () => {
    await onSeriesChanged?.();
  };

  const handleReadSeries = async (entry: SeriesSummary) => {
    if (entry.firstGroupId) {
      onOpenReader?.(entry.firstGroupId);
      return;
    }
    if (entry.cover?.groupId) {
      onOpenReader?.(entry.cover.groupId);
      return;
    }
    try {
      const fullDetail = await fetchSeriesDetail(entry.id);
      if (fullDetail.members.length > 0) {
        onOpenReader?.(fullDetail.members[0].id);
      }
    } catch {
      onOpenSeriesDetail(entry.id);
    }
  };

  const openCreateSeriesModal = async () => {
    setCreateError(null);
    setCreateTitle("");
    setCreateSearch("");
    setCreateSelectedMap(new Map());
    setIsCreateModalOpen(true);
    if (groups.length === 0) {
      try {
        setGroups(await loadAllGroups());
      } catch (reason) {
        setCreateError(reason instanceof Error ? reason.message : "Could not load manga groups.");
      }
    }
  };

  const toggleGroupForCreate = (id: string, title: string) => {
    setCreateSelectedMap((prev) => {
      const next = new Map(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.set(id, title);
        if (!createTitle.trim()) {
          setCreateTitle(title);
        }
      }
      return next;
    });
  };

  const moveCreateManga = (index: number, direction: -1 | 1) => {
    const entries = Array.from(createSelectedMap.entries());
    const target = index + direction;
    if (target < 0 || target >= entries.length) return;
    const next = [...entries];
    [next[index], next[target]] = [next[target], next[index]];
    setCreateSelectedMap(new Map(next));
  };

  const dropCreateManga = (sourceId: string, targetId: string) => {
    if (!sourceId || sourceId === targetId) return;
    const entries = Array.from(createSelectedMap.entries());
    const sourceIndex = entries.findIndex(([id]) => id === sourceId);
    const targetIndex = entries.findIndex(([id]) => id === targetId);
    if (sourceIndex === -1 || targetIndex === -1) return;
    const next = [...entries];
    const [removed] = next.splice(sourceIndex, 1);
    next.splice(targetIndex, 0, removed);
    setCreateDraggedId(null);
    setCreateDragOverId(null);
    setCreateSelectedMap(new Map(next));
  };

  const sortCreateManga = (mode: 'natural' | 'alpha-desc' | 'reverse') => {
    const entries = Array.from(createSelectedMap.entries());
    const sorted = [...entries];
    if (mode === 'natural') {
      sorted.sort((a, b) => naturalCompare(a[1], b[1]));
    } else if (mode === 'alpha-desc') {
      sorted.sort((a, b) => naturalCompare(b[1], a[1]));
    } else if (mode === 'reverse') {
      sorted.reverse();
    }
    setCreateSelectedMap(new Map(sorted));
  };

  const handleCreateNewSeries = async () => {
    const title = createTitle.trim();
    if (createSelectedMap.size < 2) {
      setCreateError("Select at least two unassigned manga.");
      return;
    }
    if (!title) {
      setCreateError("Enter a series title.");
      return;
    }
    if (isCreating) return;
    setIsCreating(true);
    setCreateError(null);
    try {
      const created = await createSeries(title, Array.from(createSelectedMap.keys()));
      setIsCreateModalOpen(false);
      setCreateTitle("");
      setCreateSelectedMap(new Map());
      seriesPageCacheRef.current.clear();
      await refreshChanged();
      onOpenSeriesDetail(created.id);
    } catch (reason) {
      setCreateError(reason instanceof Error ? reason.message : "Could not create series.");
    } finally {
      setIsCreating(false);
    }
  };

  const openAdd = async () => {
    setError(null);
    setIsAddOpen(true);
    setAddSearch("");
    setSelectedGroupIds(new Set());
    if (groups.length === 0) {
      try {
        setGroups(await loadAllGroups());
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not load manga groups.");
      }
    }
  };

  const handleAddSelectedToSeries = async () => {
    if (!detail || saving || selectedGroupIds.size === 0) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await addMangaToSeries(detail.id, Array.from(selectedGroupIds));
      setDetail(updated);
      setEditingTitle(updated.title);
      setGroups([]);
      setSelectedGroupIds(new Set());
      setIsAddOpen(false);
      await refreshChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not add manga to series.");
    } finally {
      setSaving(false);
    }
  };

  const updateMembers = async (groupIds: string[]) => {
    if (!detail || saving) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await replaceSeriesMembers(detail.id, groupIds);
      setDetail(updated);
      setEditingTitle(updated.title);
      setGroups([]);
      setSelectedGroupIds(new Set());
      setIsAddOpen(false);
      await refreshChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update series members.");
    } finally {
      setSaving(false);
    }
  };

  const moveMember = (index: number, direction: -1 | 1) => {
    if (!detail) return;
    const next = [...detail.members];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    void updateMembers(next.map((member) => member.id));
  };

  const dropMember = (sourceId: string, targetId: string) => {
    if (!detail || saving || !sourceId || sourceId === targetId) return;
    const next = reorderSeriesMembers(detail.members, sourceId, targetId);
    setDraggedMemberId(null);
    setDragOverMemberId(null);
    void updateMembers(next.map((member) => member.id));
  };

  const saveTitle = async () => {
    if (!detail || !editingTitle.trim() || saving) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await renameSeries(detail.id, editingTitle);
      setDetail(updated);
      setEditingTitle(updated.title);
      setSeries((previous) => previous.map((entry) => entry.id === updated.id ? { ...entry, title: updated.title } : entry));
      await refreshChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not rename series.");
    } finally {
      setSaving(false);
    }
  };

  const removeMember = (member: SeriesMember) => {
    if (!detail || detail.members.length <= 2) return;
    if (!window.confirm(`Remove “${member.title}” from this series?`)) return;
    void updateMembers(detail.members.filter((entry) => entry.id !== member.id).map((entry) => entry.id));
  };

  const removeSeries = async () => {
    if (!detail || saving || !window.confirm(`Delete series “${detail.title}”? Manga content will stay in the library.`)) return;
    setSaving(true);
    setError(null);
    try {
      await deleteSeries(detail.id);
      setSeries((previous) => previous.filter((entry) => entry.id !== detail.id));
      await refreshChanged();
      onCloseSeriesDetail();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete series.");
    } finally {
      setSaving(false);
    }
  };

  // ─────────────────────────────────────────────────────────────
  // Series Detail View
  // ─────────────────────────────────────────────────────────────
  if (detail || seriesId) {
    return (
      <section className="space-y-5" aria-labelledby="series-detail-title">
        {/* Top Detail Header Bar */}
        <div className="flex flex-col gap-3 rounded-xl border border-zinc-200 bg-white p-3.5 sm:p-4 dark:border-zinc-800 dark:bg-zinc-900 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2">
            <Button onClick={onCloseSeriesDetail} className="shrink-0">
              <Icon icon="carbon:arrow-left" className="mr-1 inline h-4 w-4" />
              <span>Series</span>
            </Button>
          </div>

          {detail && (
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <SeriesCover series={detail} className="h-16 w-11 sm:h-20 sm:w-14" />
              <div className="min-w-0 flex-1">
                <h2 id="series-detail-title" className="truncate text-base sm:text-lg font-bold text-zinc-900 dark:text-zinc-100" title={detail.title}>
                  {detail.title}
                </h2>
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  {detail.members.length} {detail.members.length === 1 ? "manga" : "manga in series"}
                </p>
              </div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 shrink-0">
            {detail && detail.members.length > 0 && (
              <button
                type="button"
                onClick={() => onOpenReader?.(detail.members[0].id)}
                className="inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-emerald-500 cursor-pointer transition-colors"
                title="Start reading from Chapter 1"
              >
                <Icon icon="carbon:book-open" className="h-4 w-4" />
                <span>Read Series</span>
              </button>
            )}
            {detail && (
              <Button
                onClick={() => void removeSeries()}
                className="border-red-200 text-red-600 hover:bg-red-50 dark:border-red-900/60 dark:text-red-400 dark:hover:bg-red-950/30"
              >
                <Icon icon="carbon:trash-can" className="mr-1 inline h-3.5 w-3.5" />
                <span>Delete</span>
              </Button>
            )}
          </div>
        </div>

        {loading && <p className="text-sm text-zinc-500">Loading series…</p>}
        {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300">{error}</p>}

        {detail && (
          <>
            {/* Title edit & Add Manga action bar */}
            <div className="flex flex-col gap-2 rounded-xl border border-zinc-200 bg-white p-3.5 sm:p-4 dark:border-zinc-800 dark:bg-zinc-900 sm:flex-row sm:items-end">
              <label className="min-w-0 flex-1 text-xs font-semibold text-zinc-600 dark:text-zinc-300">
                Series title
                <input
                  value={editingTitle}
                  onChange={(event) => setEditingTitle(event.target.value)}
                  maxLength={MANGA_TITLE_MAX_LENGTH}
                  className="mt-1 block w-full rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100"
                />
              </label>
              <div className="flex items-center gap-2 shrink-0">
                <Button onClick={() => void saveTitle()} disabled={saving || !editingTitle.trim()}>
                  Save title
                </Button>
                <button
                  type="button"
                  onClick={() => void openAdd()}
                  disabled={saving}
                  className="inline-flex min-h-9 items-center justify-center gap-1 rounded-lg bg-indigo-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50 cursor-pointer shadow-xs transition-colors"
                >
                  <Icon icon="carbon:add" className="h-4 w-4" />
                  <span>Add manga</span>
                </button>
              </div>
            </div>

            {/* Member list */}
            <div className="space-y-2.5">
              {detail.members.map((member, index) => (
                <article
                  key={member.id}
                  draggable={!saving}
                  onDragStart={(event) => {
                    setDraggedMemberId(member.id);
                    event.dataTransfer.effectAllowed = "move";
                    event.dataTransfer.setData("text/plain", member.id);
                  }}
                  onDragOver={(event) => {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "move";
                    setDragOverMemberId(member.id);
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    dropMember(event.dataTransfer.getData("text/plain") || draggedMemberId || "", member.id);
                  }}
                  onDragEnd={() => {
                    setDraggedMemberId(null);
                    setDragOverMemberId(null);
                  }}
                  className={`flex flex-col gap-3 rounded-xl border bg-white p-3 transition dark:bg-zinc-900 sm:flex-row sm:items-center ${
                    dragOverMemberId === member.id
                      ? "border-indigo-500 ring-2 ring-indigo-200 dark:ring-indigo-900"
                      : "border-zinc-200 dark:border-zinc-800"
                  } ${draggedMemberId === member.id ? "opacity-50" : ""}`}
                  data-manga-id={member.id}
                >
                  <div className="flex items-center gap-2.5 min-w-0 flex-1">
                    <span
                      className="cursor-grab select-none text-base text-zinc-400 active:cursor-grabbing hover:text-zinc-600 dark:hover:text-zinc-200 touch-none shrink-0"
                      title="Drag to reorder"
                      aria-hidden="true"
                    >
                      ⋮⋮
                    </span>
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-zinc-100 text-xs font-bold text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                      {index + 1}
                    </span>
                    <MemberCover member={member} />
                    <div className="min-w-0 flex-1">
                      <h3 className="break-words text-xs sm:text-sm font-semibold text-zinc-900 dark:text-zinc-100 leading-snug">
                        {member.title}
                      </h3>
                      <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                        {member.count} {member.count === 1 ? "page" : "pages"}
                      </p>
                    </div>
                  </div>

                  {/* Actions Bar */}
                  <div className="flex flex-wrap items-center justify-end gap-1.5 sm:gap-2 shrink-0 pt-2 border-t border-zinc-100 dark:border-zinc-800 sm:pt-0 sm:border-t-0">
                    <button
                      type="button"
                      onClick={() => onOpenReader?.(member.id)}
                      className="inline-flex min-h-8 items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white shadow-2xs hover:bg-emerald-500 cursor-pointer transition-colors"
                      title={`Read ${member.title}`}
                    >
                      <Icon icon="carbon:book-open" className="h-3.5 w-3.5" />
                      <span>Read</span>
                    </button>

                    <Button onClick={() => onOpenMangaDetail?.(member.id)} title="View manga details">
                      Details
                    </Button>

                    <Button
                      onClick={() => setMovingMember({ id: member.id, title: member.title })}
                      title="Move manga to another series"
                      className="text-indigo-600 dark:text-indigo-400 border-indigo-200 dark:border-indigo-900/60"
                    >
                      <Icon icon="carbon:arrows-horizontal" className="mr-1 inline h-3.5 w-3.5" />
                      <span>Move</span>
                    </Button>

                    <div className="flex items-center gap-1">
                      <Button
                        onClick={() => moveMember(index, -1)}
                        disabled={index === 0 || saving}
                        aria-label={`Move ${member.title} up`}
                        title="Move up"
                        className="px-2"
                      >
                        <Icon icon="carbon:chevron-up" className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        onClick={() => moveMember(index, 1)}
                        disabled={index === detail.members.length - 1 || saving}
                        aria-label={`Move ${member.title} down`}
                        title="Move down"
                        className="px-2"
                      >
                        <Icon icon="carbon:chevron-down" className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        onClick={() => removeMember(member)}
                        disabled={detail.members.length <= 2 || saving}
                        className="text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30 px-2"
                        aria-label={`Remove ${member.title}`}
                        title={detail.members.length <= 2 ? "Series requires at least 2 members" : `Remove ${member.title}`}
                      >
                        <Icon icon="carbon:trash-can" className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                </article>
              ))}
            </div>

            {/* Add Manga Modal Dialog */}
            {isAddOpen && (
              <div
                className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-2 sm:p-4 backdrop-blur-xs"
                role="dialog"
                aria-modal="true"
                aria-labelledby="add-manga-dialog-title"
              >
                <div className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-2xl border border-zinc-200 bg-white p-4 shadow-2xl dark:border-zinc-800 dark:bg-zinc-900 sm:p-5">
                  <div className="flex items-center justify-between gap-2 shrink-0 pb-3 border-b border-zinc-100 dark:border-zinc-800">
                    <div>
                      <h3 id="add-manga-dialog-title" className="text-base font-bold text-zinc-900 dark:text-zinc-100">
                        Add Manga to Series
                      </h3>
                      <p className="text-xs text-zinc-500 dark:text-zinc-400">
                        Series: <strong className="text-zinc-700 dark:text-zinc-200">{detail.title}</strong>
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => setIsAddOpen(false)}
                      className="rounded-lg p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
                    >
                      <Icon icon="carbon:close" className="h-5 w-5" />
                    </button>
                  </div>

                  <div className="mt-3 relative shrink-0">
                    <Icon
                      icon="carbon:search"
                      className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400 h-4 w-4"
                    />
                    <input
                      autoFocus
                      type="text"
                      value={addSearch}
                      onChange={(event) => setAddSearch(event.target.value)}
                      placeholder="Search manga to add..."
                      className="w-full rounded-xl border border-zinc-200 bg-zinc-50 py-2 pl-9 pr-3 text-xs text-zinc-900 focus:border-indigo-500 focus:bg-white focus:outline-hidden dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100"
                    />
                  </div>

                  <div className="mt-3 flex-1 overflow-y-auto max-h-72 rounded-xl border border-zinc-200 p-2 space-y-1.5 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-950/40">
                    {unassignedGroupsForAdd.length === 0 ? (
                      <p className="py-6 text-center text-xs text-zinc-500">No manga available to add.</p>
                    ) : (
                      unassignedGroupsForAdd.map((group) => {
                        const isChecked = Boolean(group.id && selectedGroupIds.has(group.id));
                        return (
                          <label
                            key={group.id}
                            className={`flex items-center justify-between gap-2.5 rounded-lg p-2 text-xs transition cursor-pointer ${
                              isChecked
                                ? "bg-indigo-50 font-medium text-indigo-900 dark:bg-indigo-950/60 dark:text-indigo-200"
                                : "text-zinc-800 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
                            }`}
                          >
                            <div className="flex items-center gap-2.5 min-w-0 flex-1">
                              <input
                                type="checkbox"
                                checked={isChecked}
                                onChange={() =>
                                  group.id &&
                                  setSelectedGroupIds((prev) => {
                                    const next = new Set(prev);
                                    next.has(group.id!) ? next.delete(group.id!) : next.add(group.id!);
                                    return next;
                                  })
                                }
                                className="h-4 w-4 rounded border-zinc-300 text-indigo-600 focus:ring-indigo-500"
                              />
                              <span className="truncate">{group.title}</span>
                            </div>
                            <span className="shrink-0 text-[11px] text-zinc-400">
                              {group.count} {group.count === 1 ? "page" : "pages"}
                            </span>
                          </label>
                        );
                      })
                    )}
                  </div>

                  <div className="mt-4 flex justify-end gap-2 shrink-0 pt-3 border-t border-zinc-100 dark:border-zinc-800">
                    <button
                      type="button"
                      onClick={() => setIsAddOpen(false)}
                      className="rounded-lg border border-zinc-200 px-3 py-1.5 text-xs font-semibold text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 cursor-pointer"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleAddSelectedToSeries()}
                      disabled={selectedGroupIds.size === 0 || saving}
                      className="rounded-lg bg-indigo-600 px-4 py-1.5 text-xs font-semibold text-white shadow-2xs hover:bg-indigo-500 disabled:opacity-50 cursor-pointer transition-colors"
                    >
                      {saving ? "Adding..." : `Add Selected (${selectedGroupIds.size})`}
                    </button>
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        {/* Move Manga Modal */}
        {movingMember && (
          <AssignToSeriesModal
            isOpen={Boolean(movingMember)}
            onClose={() => setMovingMember(null)}
            mangaId={movingMember.id}
            mangaTitle={movingMember.title}
            currentSeriesId={detail?.id}
            currentSeriesTitle={detail?.title}
            onSeriesAssigned={async () => {
              if (seriesId) await loadDetail(seriesId);
              await refreshChanged();
            }}
            onSeriesRemoved={async () => {
              if (seriesId) await loadDetail(seriesId);
              await refreshChanged();
            }}
          />
        )}
      </section>
    );
  }

  // ─────────────────────────────────────────────────────────────
  // Series Library List View
  // ─────────────────────────────────────────────────────────────
  return (
    <section className="space-y-5" aria-labelledby="series-library-title">
      {/* Top Header & Search Bar */}
      <div className="flex flex-col gap-3 rounded-xl border border-zinc-200 bg-white p-3.5 sm:p-4 dark:border-zinc-800 dark:bg-zinc-900 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 id="series-library-title" className="text-base sm:text-lg font-bold text-zinc-900 dark:text-zinc-100">
            Series
          </h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Organize manga chapters and volumes into ordered collections.
          </p>
        </div>
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
          <div className="relative flex-1 sm:w-64">
            <Icon
              icon="carbon:search"
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400 h-4 w-4"
            />
            <input
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(1);
              }}
              placeholder="Search series..."
              className="w-full rounded-xl border border-zinc-200 bg-zinc-50 py-2 pl-9 pr-3 text-xs sm:text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 focus:outline-hidden focus:ring-2 focus:ring-indigo-500/50"
            />
          </div>
          <button
            type="button"
            onClick={() => void openCreateSeriesModal()}
            className="inline-flex min-h-9 items-center justify-center gap-1.5 rounded-xl bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 transition-colors cursor-pointer"
          >
            <Icon icon="carbon:add" className="h-4 w-4" />
            <span>Create series</span>
          </button>
        </div>
      </div>

      {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300">{error}</p>}

      {loading ? (
        <p className="text-sm text-zinc-500">Loading series…</p>
      ) : series.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-zinc-200 p-8 sm:p-12 text-center text-zinc-500 dark:border-zinc-800">
          <Icon icon="carbon:folders" className="h-10 w-10 text-zinc-400 mb-2" />
          <p className="text-sm font-medium text-zinc-700 dark:text-zinc-300">No series yet</p>
          <p className="text-xs text-zinc-500 mt-1 max-w-sm">
            Create a series to organize chapters or volumes into ordered collections.
          </p>
          <button
            type="button"
            onClick={() => void openCreateSeriesModal()}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-indigo-500 shadow-xs transition-colors cursor-pointer"
          >
            <Icon icon="carbon:add" className="h-4 w-4" />
            <span>Create your first series</span>
          </button>
        </div>
      ) : (
        /* Series Grid with grid-cols-1 on mobile */
        <div className="grid grid-cols-1 gap-3 sm:gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {series.map((entry) => (
            <div
              key={entry.id}
              className="group relative flex w-full min-w-0 items-center justify-between gap-3 rounded-xl border border-zinc-200 bg-white p-3 text-left transition hover:border-indigo-400 hover:shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
            >
              {/* Clickable Card Body leading to Detail */}
              <div
                onClick={() => onOpenSeriesDetail(entry.id)}
                className="flex min-w-0 flex-1 items-center gap-3 cursor-pointer select-none"
              >
                <SeriesCover series={entry} />
                <div className="min-w-0 flex-1 pr-1">
                  <strong className="block truncate text-xs sm:text-sm font-semibold text-zinc-900 dark:text-zinc-100 group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition-colors" title={entry.title}>
                    {entry.title}
                  </strong>
                  <span className="mt-0.5 block text-xs text-zinc-500 dark:text-zinc-400">
                    {entry.memberCount} {entry.memberCount === 1 ? "manga" : "manga"}
                  </span>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex flex-col items-end gap-2 shrink-0">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleReadSeries(entry);
                  }}
                  className="inline-flex min-h-8 items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white shadow-2xs hover:bg-emerald-500 cursor-pointer transition-colors"
                  title={`Read ${entry.title} from Chapter 1`}
                >
                  <Icon icon="carbon:book-open" className="h-3.5 w-3.5" />
                  <span>Read</span>
                </button>

                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpenSeriesDetail(entry.id);
                  }}
                  className="text-[11px] font-medium text-zinc-500 hover:text-indigo-600 dark:text-zinc-400 dark:hover:text-indigo-400 cursor-pointer"
                >
                  Details →
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {total > pageSize && (
        <Pagination
          currentPage={page}
          totalPages={Math.ceil(total / pageSize)}
          totalItems={total}
          pageSize={pageSize}
          itemLabel="series"
          onPageChange={setPage}
          ariaLabel="Series pages"
        />
      )}

      {/* Create Series Modal in Series Library */}
      <CreateSeriesModal
        isCreateModalOpen={isCreateModalOpen}
        isCreating={isCreating}
        createTitle={createTitle}
        setCreateTitle={setCreateTitle}
        createSearch={createSearch}
        setCreateSearch={setCreateSearch}
        unassignedGroupsForCreate={unassignedGroupsForCreate}
        createSelectedMap={createSelectedMap}
        toggleGroupForCreate={toggleGroupForCreate}
        sortCreateManga={sortCreateManga}
        createDraggedId={createDraggedId}
        setCreateDraggedId={setCreateDraggedId}
        createDragOverId={createDragOverId}
        setCreateDragOverId={setCreateDragOverId}
        dropCreateManga={dropCreateManga}
        moveCreateManga={moveCreateManga}
        createError={createError}
        setIsCreateModalOpen={setIsCreateModalOpen}
        handleCreateNewSeries={handleCreateNewSeries}
      />
    </section>
  );
};
