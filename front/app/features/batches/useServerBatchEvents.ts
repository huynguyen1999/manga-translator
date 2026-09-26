import { startTransition, useEffect } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { MangaGroupSummary, TranslationBatch, TranslatorKey } from "@/types";
import {
  getBatchKind,
  mergeServerBatchDetails,
  mergeServerBatches,
  subscribeServerBatches,
  type ServerBatch,
  type ServerBatchSummary,
} from "@/utils/serverBatches";

type ServerBatchEventsOptions = {
  initialBatchLoadRef: { current: Promise<void> | null };
  translationBatchSnapshotRef: { current: string | null };
  optimisticBatchTranslatorsRef: { current: Map<string, TranslatorKey> };
  optimisticDismissedBatchIdsRef: { current: Set<string> };
  optimisticDeletedBatchIdsRef: { current: Set<string> };
  setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>>;
  galleryPageCacheRef: {
    current: Map<string, { groups: MangaGroupSummary[]; totalGroups: number; totalImages: number }>;
  };
  setGalleryRevision: Dispatch<SetStateAction<number>>;
  loadMangaSummaries: () => Promise<void>;
};

type ServerBatchEventHandlerOptions = Omit<ServerBatchEventsOptions, "initialBatchLoadRef">;

export const createServerBatchEventHandlers = ({
  translationBatchSnapshotRef,
  optimisticBatchTranslatorsRef,
  optimisticDismissedBatchIdsRef,
  optimisticDeletedBatchIdsRef,
  setTranslationBatches,
  galleryPageCacheRef,
  setGalleryRevision,
  loadMangaSummaries,
}: ServerBatchEventHandlerOptions) => ({
  onBatches(remote: ServerBatchSummary[]) {
    const snapshot = JSON.stringify(remote);
    if (snapshot !== translationBatchSnapshotRef.current) {
      translationBatchSnapshotRef.current = snapshot;
      const activeRemote = remote.filter((batch) => !optimisticDeletedBatchIdsRef.current.has(batch.id));

      for (const [batchId, translator] of optimisticBatchTranslatorsRef.current) {
        const serverBatch = activeRemote.find((batch) => batch.id === batchId);
        if (!serverBatch || serverBatch.settings.translator === translator) {
          optimisticBatchTranslatorsRef.current.delete(batchId);
        }
      }

      for (const batchId of optimisticDismissedBatchIdsRef.current) {
        const serverBatch = activeRemote.find((batch) => batch.id === batchId);
        if (!serverBatch || serverBatch.dismissed) {
          optimisticDismissedBatchIdsRef.current.delete(batchId);
        }
      }
      for (const batchId of optimisticDeletedBatchIdsRef.current) {
        if (!remote.some((batch) => batch.id === batchId)) {
          optimisticDeletedBatchIdsRef.current.delete(batchId);
        }
      }
      const locallyDismissed = new Set(optimisticDismissedBatchIdsRef.current);
      const optimisticTranslators = new Map(optimisticBatchTranslatorsRef.current);

      let hasNewCompletions = false;
      let hasRerenderTerminal = false;
      startTransition(() => setTranslationBatches((current) => {
        for (const remoteSummary of activeRemote) {
          const existing = current.find((batch) => batch.id === remoteSummary.id);
          if (
            getBatchKind(remoteSummary) === "rerender" &&
            (!existing || existing.status !== remoteSummary.status) &&
            (remoteSummary.status === "completed" || remoteSummary.status === "error")
          ) {
            hasRerenderTerminal = true;
          }
          if (existing && existing.status !== "completed" && remoteSummary.status === "completed") {
            hasNewCompletions = true;
          }
        }
        return mergeServerBatches(current, activeRemote, locallyDismissed, optimisticTranslators);
      }));

      if (hasNewCompletions || hasRerenderTerminal) {
        galleryPageCacheRef.current.clear();
        setGalleryRevision((revision) => revision + 1);
        void loadMangaSummaries();
      }
    }
  },
  onBatchDetails(serverBatch: ServerBatch) {
    const optimisticTranslator = optimisticBatchTranslatorsRef.current.get(serverBatch.id);
    setTranslationBatches((current) => current.map((batch) => {
      if (batch.id !== serverBatch.id || !(
        batch.detailsLoaded || batch.items.length > 0 || serverBatch.status === "completed"
      )) return batch;
      const detailed = mergeServerBatchDetails(batch, serverBatch);
      return optimisticTranslator && optimisticTranslator !== serverBatch.settings.translator
        ? { ...detailed, settings: { ...detailed.settings, translator: optimisticTranslator } }
        : detailed;
    }));
  },
});

export const useServerBatchEvents = ({
  initialBatchLoadRef,
  translationBatchSnapshotRef,
  optimisticBatchTranslatorsRef,
  optimisticDismissedBatchIdsRef,
  optimisticDeletedBatchIdsRef,
  setTranslationBatches,
  galleryPageCacheRef,
  setGalleryRevision,
  loadMangaSummaries,
}: ServerBatchEventsOptions) => {
  useEffect(() => {
    let disposed = false;
    let source: EventSource | undefined;
    const startSubscription = async () => {
      if (initialBatchLoadRef.current) await initialBatchLoadRef.current;
      if (!disposed) {
        const handlers = createServerBatchEventHandlers({
          translationBatchSnapshotRef,
          optimisticBatchTranslatorsRef,
          optimisticDismissedBatchIdsRef,
          optimisticDeletedBatchIdsRef,
          setTranslationBatches,
          galleryPageCacheRef,
          setGalleryRevision,
          loadMangaSummaries,
        });
        source = subscribeServerBatches(
          handlers.onBatches,
          () => console.warn("Batch event stream disconnected; retrying..."),
          handlers.onBatchDetails,
        );
      }
    };
    void startSubscription();
    return () => {
      disposed = true;
      source?.close();
    };
  }, [loadMangaSummaries]);
};
