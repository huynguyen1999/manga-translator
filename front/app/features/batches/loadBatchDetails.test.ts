import assert from "node:assert/strict";
import type { Dispatch, SetStateAction } from "react";
import type { TranslationBatch } from "@/types";
import type { ServerBatch } from "@/utils/serverBatches";
import { loadBatchDetails } from "./loadBatchDetails";

const batchId = "batch-1";
const existing = { id: batchId, settings: { translator: "deepseek" } } as TranslationBatch;
let batches = [existing];
const requestsRef = { current: new Map<string, Promise<void>>() };
const optimisticTranslatorsRef = { current: new Map([[batchId, "openai" as const]]) };
const setBatches: Dispatch<SetStateAction<TranslationBatch[]>> = (update) => {
  batches = typeof update === "function" ? update(batches) : update;
};

let resolveFetch!: (batch: ServerBatch) => void;
let fetchCount = 0;
const fetchBatch = () => {
  fetchCount += 1;
  return new Promise<ServerBatch>((resolve) => { resolveFetch = resolve; });
};
const serverBatch = {
  id: batchId,
  settings: { translator: "deepseek" },
} as ServerBatch;
const converted = { id: batchId, settings: { translator: "deepseek" } } as TranslationBatch;

const first = loadBatchDetails(
  batchId,
  requestsRef,
  optimisticTranslatorsRef,
  setBatches,
  fetchBatch,
  () => converted,
);
const second = loadBatchDetails(
  batchId,
  requestsRef,
  optimisticTranslatorsRef,
  setBatches,
  fetchBatch,
  () => converted,
);
assert.equal(first, second, "concurrent requests for one batch share a promise");
assert.equal(fetchCount, 1);

resolveFetch(serverBatch);
await first;
assert.equal(batches[0].settings.translator, "openai", "optimistic translator survives stale server detail");
assert.equal(optimisticTranslatorsRef.current.get(batchId), "openai");
assert.equal(requestsRef.current.has(batchId), false, "completed request is removed from the dedupe map");
