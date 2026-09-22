import type { TranslationBatch, TranslationBatchStatus } from "@/types";

export const findNextTranslationBatch = (
  batches: TranslationBatch[]
): TranslationBatch | undefined => batches.find((batch) => batch.status === "waiting");

export const findActiveTranslationBatch = (
  batches: TranslationBatch[]
): TranslationBatch | undefined =>
  batches.find((batch) => batch.status === "processing" || batch.status === "paused");

export const getTerminalBatchStatus = (
  batch: TranslationBatch
): Extract<TranslationBatchStatus, "completed" | "error"> | null => {
  if (batch.items.some((item) => item.status === "queued" || item.status === "processing")) {
    return null;
  }
  return batch.items.some((item) => item.status === "error") ? "error" : "completed";
};
