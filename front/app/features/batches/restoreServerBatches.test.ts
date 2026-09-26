import assert from "node:assert/strict";
import type { Dispatch, SetStateAction } from "react";
import type { TranslationBatch } from "@/types";
import type { ServerBatchSummary } from "@/utils/serverBatches";
import { restoreServerBatches } from "./restoreServerBatches";

const remoteBatch = (id: string) => ({ id } as unknown as ServerBatchSummary);
const upload = {
  id: "upload-1",
  status: "uploading",
  kind: "manga-upload",
  items: [],
} as unknown as TranslationBatch;
const detailedUpload = { ...upload, items: [{ file: new File(["page"], "page.png") }] } as TranslationBatch;

let batches: TranslationBatch[] = [];
const setBatches: Dispatch<SetStateAction<TranslationBatch[]>> = (next) => {
  batches = typeof next === "function" ? (next as (previous: TranslationBatch[]) => TranslationBatch[])(batches) : next;
};
const events: string[] = [];
const snapshotRef = { current: null as string | null };

await restoreServerBatches({
  translationBatchSnapshotRef: snapshotRef,
  optimisticDeletedBatchIdsRef: { current: new Set(["deleted"]) },
  setTranslationBatches: setBatches,
  resumeStudioMangaUpload: async (batch) => { events.push(`resume-manga:${batch.id}`); },
  resumeStudioTranslationUpload: async (batch) => { events.push(`resume-translation:${batch.id}`); },
}, {
  loadStoredBatches: async () => { events.push("load-local"); return [upload]; },
  fetchRemoteBatches: async () => { events.push("fetch-server"); return [remoteBatch("kept"), remoteBatch("deleted")]; },
  convertBatch: (batch) => ({
    id: batch.id,
    mangaTitle: batch.id,
    addedAt: new Date(0),
    settings: {},
    items: [],
    totalItems: 0,
    completedCount: 0,
    status: "waiting",
  }) as unknown as TranslationBatch,
  clearStoredQueue: async () => { events.push("clear-local"); },
  saveStoredBatch: async (batch) => { events.push(`save:${batch.id}`); },
  loadStoredBatch: async () => detailedUpload,
});

assert.deepEqual(events.slice(0, 3), ["load-local", "fetch-server", "clear-local"]);
assert.deepEqual(events.slice(-2), ["save:upload-1", "resume-manga:upload-1"]);
assert.deepEqual(batches.map((batch) => batch.id), ["upload-1", "kept"]);
assert.equal(batches[0].items.length, 1);
assert.deepEqual(JSON.parse(snapshotRef.current || "[]").map((batch: ServerBatchSummary) => batch.id), ["kept"]);

console.log("server batch restoration contracts passed");
