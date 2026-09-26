import type { Dispatch, SetStateAction } from "react";
import type { TranslationBatch } from "@/types";
import {
  loadTranslationBatchFromIDB,
  removeTranslationBatchFromIDB,
} from "@/utils/fileStorage";
import { submitServerBatch, toTranslationBatch } from "@/utils/serverBatches";

type TranslationBatchUploadOptions = {
  setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>>;
  studioUploadRequestsRef: { current: Map<string, Promise<void>> };
};

export const createTranslationBatchUploadActions = ({
  setTranslationBatches,
  studioUploadRequestsRef,
}: TranslationBatchUploadOptions) => {
  const failStudioTranslationUpload = async (uploadBatch: TranslationBatch, error: unknown) => {
    const errorMessage = error instanceof Error ? error.message : "Failed to submit translation batch. Please try again.";
    const failedBatch = {
      ...uploadBatch,
      status: "error" as const,
      error: errorMessage,
      items: uploadBatch.items.map((item) => ({
        ...item,
        status: "error" as const,
        error: errorMessage,
      })),
    };
    setTranslationBatches((prev) => prev.map((batch) =>
      batch.id === uploadBatch.id ? failedBatch : batch
    ));
    // Do not restore a failed import as an active upload on the next page load.
    await removeTranslationBatchFromIDB(uploadBatch.id).catch((removeError) =>
      console.warn(`Failed to remove failed translation upload ${uploadBatch.id}:`, removeError)
    );
  };

  const resumeStudioTranslationUpload = (uploadBatch: TranslationBatch): Promise<void> => {
    const pending = studioUploadRequestsRef.current.get(uploadBatch.id);
    if (pending) return pending;

    const performUpload = async (batch: TranslationBatch) => {
      try {
        const serverBatch = await submitServerBatch(
          batch,
          (uploadProgress) => setTranslationBatches((prev) => prev.map((batchItem) =>
            batchItem.id === uploadBatch.id ? { ...batchItem, uploadProgress } : batchItem
          )),
        );
        await removeTranslationBatchFromIDB(uploadBatch.id);
        setTranslationBatches((prev) => [
          toTranslationBatch(serverBatch),
          ...prev.filter((batchItem) => batchItem.id !== uploadBatch.id && batchItem.id !== serverBatch.id),
        ]);
      } catch (error) {
        await failStudioTranslationUpload(uploadBatch, error);
        console.warn(`Failed to submit translation batch ${uploadBatch.id}:`, error);
      }
    };

    const request = (async () => {
      const runUpload = async () => {
        const persisted = await loadTranslationBatchFromIDB(uploadBatch.id);
        if (!persisted) {
          if (uploadBatch.items.some((item) => item.file.size > 0)) {
            await performUpload(uploadBatch);
          } else {
            setTranslationBatches((prev) => prev.filter((batch) => batch.id !== uploadBatch.id));
          }
          return;
        }
        await performUpload(persisted);
      };

      if (typeof navigator !== "undefined" && navigator.locks?.request) {
        await navigator.locks.request(`translation-batch-upload:${uploadBatch.id}`, runUpload);
      } else {
        await runUpload();
      }
    })();
    studioUploadRequestsRef.current.set(uploadBatch.id, request);
    void request.then(
      () => studioUploadRequestsRef.current.delete(uploadBatch.id),
      () => studioUploadRequestsRef.current.delete(uploadBatch.id),
    );
    return request;
  };

  return { failStudioTranslationUpload, resumeStudioTranslationUpload };
};
