import assert from "node:assert/strict";
import type { TranslationBatch } from "@/types";
import { mergeServerBatches, type ServerBatchSummary } from "../utils/serverBatches";

const localUpload = {
  id: "local-upload",
  addedAt: new Date(1),
  mangaTitle: "Chapter 2",
  settings: {} as TranslationBatch["settings"],
  items: [],
  totalItems: 1,
  completedCount: 0,
  status: "uploading",
} as TranslationBatch;
const remoteSummary: ServerBatchSummary = {
  id: "server-batch",
  title: "Chapter 1",
  mangaTitle: "Chapter 1",
  addedAt: 2,
  settings: {} as ServerBatchSummary["settings"],
  status: "processing",
  dismissed: false,
  totalItems: 3,
  completedCount: 1,
  queuedCount: 1,
  processingCount: 1,
  failedCount: 0,
  needsReviewCount: 0,
  currentStage: "ocr",
  currentStagePassedCount: 2,
};

const batches = mergeServerBatches([localUpload], [remoteSummary], new Set());
assert.deepEqual(batches.map(({ id }) => id), ["local-upload", "server-batch"]);
assert.equal(batches[0].status, "uploading");
assert.equal(batches[1].status, "processing");
assert.equal(batches[1].currentStage, "ocr");
assert.equal(batches[1].currentStagePassedCount, 2);

console.log("batch flow contracts passed");
