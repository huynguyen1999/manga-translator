import type { Dispatch, SetStateAction } from "react";
import type { TranslationBatch } from "@/types";
import {
  clearQueueFromIDB,
  loadTranslationBatchFromIDB,
  loadTranslationBatchesFromIDB,
  saveTranslationBatchToIDB,
} from "@/utils/fileStorage";
import {
  fetchServerBatches,
  toTranslationBatch,
} from "@/utils/serverBatches";

type BatchRestoreOptions = {
  translationBatchSnapshotRef: { current: string | null };
  optimisticDeletedBatchIdsRef: { current: Set<string> };
  setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>>;
  resumeStudioMangaUpload: (batch: TranslationBatch) => Promise<void>;
  resumeStudioTranslationUpload: (batch: TranslationBatch) => Promise<void>;
};

type BatchRestoreDependencies = {
  loadStoredBatches: typeof loadTranslationBatchesFromIDB;
  fetchRemoteBatches: typeof fetchServerBatches;
  convertBatch: typeof toTranslationBatch;
  clearStoredQueue: typeof clearQueueFromIDB;
  saveStoredBatch: typeof saveTranslationBatchToIDB;
  loadStoredBatch: typeof loadTranslationBatchFromIDB;
};

const defaultDependencies: BatchRestoreDependencies = {
  loadStoredBatches: loadTranslationBatchesFromIDB,
  fetchRemoteBatches: fetchServerBatches,
  convertBatch: toTranslationBatch,
  clearStoredQueue: clearQueueFromIDB,
  saveStoredBatch: saveTranslationBatchToIDB,
  loadStoredBatch: loadTranslationBatchFromIDB,
};

export const restoreServerBatches = async (
  {
    translationBatchSnapshotRef,
    optimisticDeletedBatchIdsRef,
    setTranslationBatches,
    resumeStudioMangaUpload,
    resumeStudioTranslationUpload,
  }: BatchRestoreOptions,
  dependencies: BatchRestoreDependencies = defaultDependencies,
) => {
  try {
    const storedBatchesPromise = dependencies.loadStoredBatches();
    const remote = await dependencies.fetchRemoteBatches();
    translationBatchSnapshotRef.current = JSON.stringify(remote);
    setTranslationBatches(remote.map(dependencies.convertBatch));

    const storedBatches = await storedBatchesPromise;
    const uploadingBatches = storedBatches.filter((batch) => batch.status === "uploading");
    await dependencies.clearStoredQueue().catch(() => {});
    for (const upload of uploadingBatches) {
      await dependencies.saveStoredBatch(upload).catch(() => {});
    }

    const activeRemote = remote.filter((batch) => !optimisticDeletedBatchIdsRef.current.has(batch.id));
    translationBatchSnapshotRef.current = JSON.stringify(activeRemote);
    setTranslationBatches([
      ...uploadingBatches,
      ...activeRemote.map(dependencies.convertBatch),
    ]);
    for (const batch of uploadingBatches) {
      const detailed = batch.items.some((item) => item.file.size > 0)
        ? batch
        : await dependencies.loadStoredBatch(batch.id);
      if (!detailed) continue;
      setTranslationBatches((current) => current.map((candidate) =>
        candidate.id === detailed.id ? detailed : candidate
      ));
      if (detailed.kind === "manga-upload") {
        await resumeStudioMangaUpload(detailed);
      } else {
        await resumeStudioTranslationUpload(detailed);
      }
    }
  } catch (error) {
    console.warn("Failed to restore server batches:", error);
  }
};
