import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { TranslationBatch, TranslatorKey } from "@/types";
import { renameBatchQueuedOnly } from "@/utils/batchRename";
import {
  batchAction,
  canChangeBatchTranslator,
  removeBatchItem,
  removeServerBatch,
  retryBatchItem,
  toTranslationBatch,
  updateBatchManualReview,
  updateBatchPriority,
  updateBatchTitle,
  updateBatchTranslator,
} from "@/utils/serverBatches";
import { removeTranslationBatchFromIDB } from "@/utils/fileStorage";

type BatchActionsOptions = {
  translationBatches: TranslationBatch[];
  setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>>;
  batchMutationVersionRef: { current: number };
  optimisticBatchTranslatorsRef: { current: Map<string, TranslatorKey> };
  optimisticDismissedBatchIdsRef: { current: Set<string> };
  optimisticDeletedBatchIdsRef: { current: Set<string> };
  studioUploadRequestsRef: { current: Map<string, Promise<void>> };
  resumeStudioMangaUpload: (batch: TranslationBatch) => Promise<void>;
};

export const useBatchActions = ({
  translationBatches,
  setTranslationBatches,
  batchMutationVersionRef,
  optimisticBatchTranslatorsRef,
  optimisticDismissedBatchIdsRef,
  optimisticDeletedBatchIdsRef,
  studioUploadRequestsRef,
  resumeStudioMangaUpload,
}: BatchActionsOptions) => {
  const updateTranslationBatchTranslator = useCallback(
    (batchId: string, translator: TranslatorKey) => {
      const batch = translationBatches.find((candidate) => candidate.id === batchId);
      if (!batch || !canChangeBatchTranslator(batch)) return;
      optimisticBatchTranslatorsRef.current.set(batchId, translator);
      setTranslationBatches((prev) =>
        prev.map((batch) =>
          batch.id === batchId
            ? { ...batch, settings: { ...batch.settings, translator } }
            : batch
        )
      );
      void updateBatchTranslator(batchId, translator).catch((error) => {
        if (optimisticBatchTranslatorsRef.current.get(batchId) === translator) {
          optimisticBatchTranslatorsRef.current.delete(batchId);
        }
        console.warn("Failed to update batch translator:", error);
      });
    },
    [translationBatches]
  );

  const updateTranslationBatchManualReview = useCallback(
    (batchId: string, enabled: boolean) => {
      setTranslationBatches((prev) =>
        prev.map((batch) =>
          batch.id === batchId
            ? { ...batch, settings: { ...batch.settings, keepFailedPagesForEditing: enabled } }
            : batch
        )
      );
      void updateBatchManualReview(batchId, enabled).catch((error) =>
        console.warn("Failed to update manual review setting:", error)
      );
    },
    []
  );

  const updateTranslationBatchPriority = useCallback(
    async (batchId: string, priority: boolean) => {
      setTranslationBatches((prev) =>
        prev.map((batch) =>
          batch.id === batchId && batch.status === "waiting"
            ? { ...batch, priority }
            : batch
        )
      );
      const serverBatch = await updateBatchPriority(batchId, priority);
      setTranslationBatches((prev) => [
        toTranslationBatch(serverBatch),
        ...prev.filter((batch) => batch.id !== serverBatch.id),
      ]);
    },
    []
  );

  const pauseTranslation = useCallback(async (batchId: string) => {
    const activeBatch = translationBatches.find((batch) => batch.id === batchId && batch.status === "processing");
    if (!activeBatch) return;
    batchMutationVersionRef.current += 1;
    try {
      const serverBatch = await batchAction(activeBatch.id, "pause");
      setTranslationBatches((prev) => [
        toTranslationBatch(serverBatch),
        ...prev.filter((batch) => batch.id !== serverBatch.id),
      ]);
    } catch (error) {
      console.warn("Failed to pause batch:", error);
      throw error;
    }
  }, [translationBatches]);

  const resumeTranslation = useCallback(async (batchId: string) => {
    const activeBatch = translationBatches.find((batch) => batch.id === batchId && batch.status === "paused");
    if (!activeBatch) return;
    batchMutationVersionRef.current += 1;
    try {
      const serverBatch = await batchAction(activeBatch.id, "resume");
      setTranslationBatches((prev) => [
        toTranslationBatch(serverBatch),
        ...prev.filter((batch) => batch.id !== serverBatch.id),
      ]);
    } catch (error) {
      console.warn("Failed to resume batch:", error);
      throw error;
    }
  }, [translationBatches]);

  const dismissTranslationBatch = useCallback((batchId: string) => {
    optimisticDismissedBatchIdsRef.current.add(batchId);
    setTranslationBatches((prev) => prev.map((batch) =>
      batch.id === batchId ? { ...batch, dismissed: true } : batch
    ));
    void removeTranslationBatchFromIDB(batchId).catch(() => {});
    if (batchId.startsWith("upload-") || batchId.startsWith("legacy-") || batchId.startsWith("original-")) {
      // If it's a client or imported batch, try dismiss on server if exists, but don't revert on 404
      void batchAction(batchId, "dismiss").catch(() => {});
      return;
    }
    void batchAction(batchId, "dismiss").catch((error) => {
      const status = (error as { status?: number })?.status;
      if (status !== 404 && !String(error).includes("404")) {
        optimisticDismissedBatchIdsRef.current.delete(batchId);
        setTranslationBatches((prev) => prev.map((batch) =>
          batch.id === batchId ? { ...batch, dismissed: false } : batch
        ));
        console.warn("Failed to dismiss batch:", error);
      }
    });
  }, []);

  const removeTranslationBatch = useCallback((batchId: string) => {
    batchMutationVersionRef.current += 1;
    optimisticDeletedBatchIdsRef.current.add(batchId);
    setTranslationBatches((prev) => prev.filter((batch) => batch.id !== batchId));
    void removeTranslationBatchFromIDB(batchId).catch(() => {});

    const pendingUpload = studioUploadRequestsRef.current.get(batchId);
    if (pendingUpload) {
      studioUploadRequestsRef.current.delete(batchId);
    }

    if (batchId.startsWith("upload-") || batchId.startsWith("legacy-")) {
      return Promise.resolve();
    }
    return removeServerBatch(batchId).catch((error) => {
      const status = (error as { status?: number })?.status;
      if (status !== 404 && !String(error).includes("404")) {
        optimisticDeletedBatchIdsRef.current.delete(batchId);
        console.warn("Failed to remove batch:", error);
        throw error;
      }
    });
  }, []);

  const removeTranslationItem = (batchId: string, itemId: string) =>
    removeBatchItem(batchId, itemId);

  const retryTranslationItem = (
    batchId: string,
    itemId: string,
    keepFailedPagesForEditing = false,
    fromStage?: string,
  ) => {
    const targetBatch = translationBatches.find((batch) => batch.id === batchId);
    if (targetBatch?.kind === "manga-upload") {
      if (fromStage) throw new Error("Stage retries are only available for translated pages.");
      return resumeStudioMangaUpload(targetBatch);
    }
    batchMutationVersionRef.current += 1;
    return retryBatchItem(batchId, itemId, keepFailedPagesForEditing, fromStage).catch((error) => {
      console.warn("Failed to retry batch item:", error);
      throw error;
    }).then((serverBatch) => {
      setTranslationBatches((prev) => [
        toTranslationBatch(serverBatch),
        ...prev.filter((batch) => batch.id !== serverBatch.id),
      ]);
    });
  };

  const handleBatchMangaTitleChange = useCallback(
    (batchId: string, title: string) => {
      setTranslationBatches((prev) =>
        prev.map((batch) => (batch.id === batchId ? renameBatchQueuedOnly(batch, title) : batch))
      );
      void updateBatchTitle(batchId, title).catch((error) =>
        console.warn("Failed to update batch title:", error)
      );
    },
    []
  );

  return {
    handleBatchMangaTitleChange,
    updateTranslationBatchTranslator,
    updateTranslationBatchManualReview,
    updateTranslationBatchPriority,
    pauseTranslation,
    resumeTranslation,
    dismissTranslationBatch,
    removeTranslationBatch,
    removeTranslationItem,
    retryTranslationItem,
  };
};
