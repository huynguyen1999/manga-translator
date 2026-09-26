import { useEffect, type Dispatch, type SetStateAction } from 'react';
import type { FileStatus, StudioFile, TranslationBatch } from '@/types';
import { loadStudioStateFromIDB } from '@/utils/fileStorage';
import { applyRestoredStudioState } from './applyRestoredStudioState';
import { restoreServerBatches } from '@/features/batches/restoreServerBatches';

interface StudioInitializationOptions {
  hydrateSettings: () => void;
  studioState: {
    setFiles: Dispatch<SetStateAction<StudioFile[]>>;
    setFileStatuses: Dispatch<SetStateAction<Map<string, FileStatus>>>;
    setSelectedFiles: Dispatch<SetStateAction<Set<string>>>;
    setExcludedColorFiles: Dispatch<SetStateAction<Set<string>>>;
    setAutoDetectedColorFiles: Dispatch<SetStateAction<Set<string>>>;
    setIsStudioHydrated: Dispatch<SetStateAction<boolean>>;
    studioDropOrderRef: { current: number };
    folderMapRef: { current: Map<string, string> };
    resultUrlsRef: { current: Set<string> };
  };
  batches: {
    initialBatchLoadRef: { current: Promise<void> | null };
    translationBatchSnapshotRef: { current: string | null };
    optimisticDeletedBatchIdsRef: { current: Set<string> };
    setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>>;
    resumeStudioMangaUpload: (batch: TranslationBatch) => Promise<void>;
    resumeStudioTranslationUpload: (batch: TranslationBatch) => Promise<void>;
  };
}

export function useStudioInitialization({ hydrateSettings, studioState, batches }: StudioInitializationOptions) {
  useEffect(() => {
    hydrateSettings();
    loadStudioStateFromIDB()
      .then((restored) => {
        applyRestoredStudioState(restored, studioState);
      })
      .catch((error) => {
        console.warn('Failed to restore studio state from IDB:', error);
        studioState.setIsStudioHydrated(true);
      });

    batches.initialBatchLoadRef.current = restoreServerBatches({
      translationBatchSnapshotRef: batches.translationBatchSnapshotRef,
      optimisticDeletedBatchIdsRef: batches.optimisticDeletedBatchIdsRef,
      setTranslationBatches: batches.setTranslationBatches,
      resumeStudioMangaUpload: batches.resumeStudioMangaUpload,
      resumeStudioTranslationUpload: batches.resumeStudioTranslationUpload,
    });
    void batches.initialBatchLoadRef.current;
  }, []);
}
