import type { Dispatch, SetStateAction } from "react";
import type { TranslationBatch, TranslatorKey } from "@/types";
import {
  fetchServerBatch,
  toTranslationBatch,
  type ServerBatch,
} from "@/utils/serverBatches";

type Ref<T> = { current: T };

export function loadBatchDetails(
  batchId: string,
  requestsRef: Ref<Map<string, Promise<void>>>,
  optimisticTranslatorsRef: Ref<Map<string, TranslatorKey>>,
  setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>>,
  fetchBatch: (id: string) => Promise<ServerBatch> = fetchServerBatch,
  convertBatch: (batch: ServerBatch) => TranslationBatch = toTranslationBatch,
): Promise<void> {
  const existingRequest = requestsRef.current.get(batchId);
  if (existingRequest) return existingRequest;

  const request = fetchBatch(batchId)
    .then((serverBatch) => {
      const optimisticTranslator = optimisticTranslatorsRef.current.get(batchId);
      if (optimisticTranslator === serverBatch.settings.translator) {
        optimisticTranslatorsRef.current.delete(batchId);
      }
      const detailed = convertBatch(serverBatch);
      const withOptimisticTranslator = optimisticTranslator && optimisticTranslator !== serverBatch.settings.translator
        ? { ...detailed, settings: { ...detailed.settings, translator: optimisticTranslator } }
        : detailed;
      setTranslationBatches((previous) => previous.map((batch) =>
        batch.id === batchId ? withOptimisticTranslator : batch
      ));
    })
    .finally(() => {
      requestsRef.current.delete(batchId);
    });
  requestsRef.current.set(batchId, request);
  return request;
}
