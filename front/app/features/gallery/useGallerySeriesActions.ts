import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { SeriesSummary } from '@/types';
import { naturalCompare } from '@/utils/resultGallery';
import { addMangaToSeries, createSeries, fetchAllSeries } from '@/utils/series';
import type { CreatedSeriesToast } from './MangaSeriesSelectionDock';

interface UseGallerySeriesActionsArgs {
  selectedMangaIds: Map<string, string>;
  setSelectedMangaIds: Dispatch<SetStateAction<Map<string, string>>>;
  onSeriesChanged?: () => void | Promise<void>;
  onOpenSeriesDetail?: (seriesId: string) => void;
}

export function useGallerySeriesActions({
  selectedMangaIds,
  setSelectedMangaIds,
  onSeriesChanged,
  onOpenSeriesDetail,
}: UseGallerySeriesActionsArgs) {
  const [isCreateSeriesOpen, setIsCreateSeriesOpen] = useState(false);
  const [newSeriesTitle, setNewSeriesTitle] = useState('');
  const [seriesError, setSeriesError] = useState<string | null>(null);
  const [isCreatingSeries, setIsCreatingSeries] = useState(false);
  const [openSeriesAfterCreate, setOpenSeriesAfterCreate] = useState(false);
  const [createSeriesDraggedId, setCreateSeriesDraggedId] = useState<string | null>(null);
  const [createSeriesDragOverId, setCreateSeriesDragOverId] = useState<string | null>(null);
  const [createdSeriesToast, setCreatedSeriesToast] = useState<CreatedSeriesToast | null>(null);
  const [seriesModalMode, setSeriesModalMode] = useState<'create' | 'add'>('create');
  const [allExistingSeries, setAllExistingSeries] = useState<SeriesSummary[]>([]);
  const [isLoadingExistingSeries, setIsLoadingExistingSeries] = useState(false);
  const [existingSeriesSearch, setExistingSeriesSearch] = useState('');
  const [targetExistingSeriesId, setTargetExistingSeriesId] = useState<string | null>(null);

  const loadGalleryAllSeries = useCallback(async () => {
    setIsLoadingExistingSeries(true);
    try {
      const list = await fetchAllSeries();
      setAllExistingSeries(list);
      if (list.length > 0) {
        setTargetExistingSeriesId((current) => current || list[0].id);
      }
    } catch (err) {
      console.warn('Failed to load existing series list:', err);
    } finally {
      setIsLoadingExistingSeries(false);
    }
  }, []);

  const filteredExistingSeries = useMemo(() => {
    const q = existingSeriesSearch.trim().toLowerCase();
    if (!q) return allExistingSeries;
    return allExistingSeries.filter((series) => series.title.toLowerCase().includes(q));
  }, [allExistingSeries, existingSeriesSearch]);

  useEffect(() => {
    if (!createdSeriesToast) return;
    const timer = window.setTimeout(() => setCreatedSeriesToast(null), 6000);
    return () => window.clearTimeout(timer);
  }, [createdSeriesToast]);

  const moveSelectedManga = (index: number, direction: -1 | 1) => {
    const entries = Array.from(selectedMangaIds.entries());
    const target = index + direction;
    if (target < 0 || target >= entries.length) return;
    const next = [...entries];
    [next[index], next[target]] = [next[target], next[index]];
    setSelectedMangaIds(new Map(next));
  };

  const dropSelectedManga = (sourceId: string, targetId: string) => {
    if (!sourceId || sourceId === targetId) return;
    const entries = Array.from(selectedMangaIds.entries());
    const sourceIndex = entries.findIndex(([id]) => id === sourceId);
    const targetIndex = entries.findIndex(([id]) => id === targetId);
    if (sourceIndex === -1 || targetIndex === -1) return;
    const next = [...entries];
    const [removed] = next.splice(sourceIndex, 1);
    next.splice(targetIndex, 0, removed);
    setCreateSeriesDraggedId(null);
    setCreateSeriesDragOverId(null);
    setSelectedMangaIds(new Map(next));
  };

  const sortSelectedManga = (mode: 'natural' | 'alpha-desc' | 'reverse') => {
    const entries = Array.from(selectedMangaIds.entries());
    const sorted = [...entries];
    if (mode === 'natural') {
      sorted.sort((a, b) => naturalCompare(a[1], b[1]));
    } else if (mode === 'alpha-desc') {
      sorted.sort((a, b) => naturalCompare(b[1], a[1]));
    } else {
      sorted.reverse();
    }
    setSelectedMangaIds(new Map(sorted));
  };

  const handleCreateSeries = async () => {
    const title = newSeriesTitle.trim();
    if (selectedMangaIds.size < 2) {
      setSeriesError('Select at least two unassigned manga.');
      return;
    }
    if (!title) {
      setSeriesError('Enter a series title.');
      return;
    }
    if (isCreatingSeries) return;
    setIsCreatingSeries(true);
    setSeriesError(null);
    try {
      const selectedCount = selectedMangaIds.size;
      const created = await createSeries(title, [...selectedMangaIds.keys()]);
      setSelectedMangaIds(new Map());
      setNewSeriesTitle('');
      setIsCreateSeriesOpen(false);
      await onSeriesChanged?.();
      if (openSeriesAfterCreate) {
        onOpenSeriesDetail?.(created.id);
      } else {
        setCreatedSeriesToast({ seriesId: created.id, title: created.title, count: selectedCount });
      }
    } catch (reason) {
      setSeriesError(reason instanceof Error ? reason.message : 'Could not create series.');
    } finally {
      setIsCreatingSeries(false);
    }
  };

  const handleAddToExistingSeries = async () => {
    if (!targetExistingSeriesId) {
      setSeriesError('Please select an existing series to add to.');
      return;
    }
    if (selectedMangaIds.size === 0) {
      setSeriesError('Please select at least one manga.');
      return;
    }
    if (isCreatingSeries) return;
    setIsCreatingSeries(true);
    setSeriesError(null);
    try {
      const targetId = targetExistingSeriesId;
      const targetObj = allExistingSeries.find((series) => series.id === targetId);
      const updated = await addMangaToSeries(targetId, Array.from(selectedMangaIds.keys()));
      setSelectedMangaIds(new Map());
      setIsCreateSeriesOpen(false);
      await onSeriesChanged?.();
      if (openSeriesAfterCreate) {
        onOpenSeriesDetail?.(targetId);
      } else {
        setCreatedSeriesToast({
          seriesId: targetId,
          title: updated.title || targetObj?.title || 'Series',
          count: updated.members.length,
        });
      }
    } catch (reason) {
      setSeriesError(reason instanceof Error ? reason.message : 'Could not add manga to series.');
    } finally {
      setIsCreatingSeries(false);
    }
  };

  return {
    isCreateSeriesOpen,
    setIsCreateSeriesOpen,
    newSeriesTitle,
    setNewSeriesTitle,
    seriesError,
    setSeriesError,
    isCreatingSeries,
    openSeriesAfterCreate,
    setOpenSeriesAfterCreate,
    createSeriesDraggedId,
    setCreateSeriesDraggedId,
    createSeriesDragOverId,
    setCreateSeriesDragOverId,
    createdSeriesToast,
    setCreatedSeriesToast,
    seriesModalMode,
    setSeriesModalMode,
    isLoadingExistingSeries,
    existingSeriesSearch,
    setExistingSeriesSearch,
    targetExistingSeriesId,
    setTargetExistingSeriesId,
    loadGalleryAllSeries,
    filteredExistingSeries,
    moveSelectedManga,
    dropSelectedManga,
    sortSelectedManga,
    handleCreateSeries,
    handleAddToExistingSeries,
  };
}
