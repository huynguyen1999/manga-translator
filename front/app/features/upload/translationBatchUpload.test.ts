import assert from "node:assert/strict";
import type { Dispatch, SetStateAction } from "react";
import type { TranslationBatch } from "@/types";
import { createTranslationBatchUploadActions } from "./translationBatchUpload";

const batch: TranslationBatch = {
  id: "upload-1",
  addedAt: new Date(0),
  mangaTitle: "Chapter 1",
  settings: {} as TranslationBatch["settings"],
  items: [{
    id: "page-1",
    file: new File([], "page.png", { type: "image/png" }),
    addedAt: new Date(0),
    status: "queued",
  }],
  totalItems: 1,
  completedCount: 0,
  status: "uploading",
};
let batches = [batch];
const setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>> = (action) => {
  batches = typeof action === "function" ? action(batches) : action;
};
const studioUploadRequestsRef = { current: new Map<string, Promise<void>>() };
const actions = createTranslationBatchUploadActions({ setTranslationBatches, studioUploadRequestsRef });

await actions.failStudioTranslationUpload(batch, new Error("upload failed"));
assert.equal(batches[0].status, "error");
assert.equal((batches[0] as TranslationBatch & { error?: string }).error, "upload failed");
assert.equal(batches[0].items[0].error, "upload failed");

const originalWindow = (globalThis as any).window;
const originalIndexedDB = (globalThis as any).indexedDB;
const database = {
  objectStoreNames: { contains: () => false },
  createObjectStore: () => ({}),
  close: () => {},
};
const indexedDB = {
  open: () => {
    const request: any = { result: database };
    queueMicrotask(() => {
      request.onupgradeneeded?.({ target: request });
      request.onsuccess?.({ target: request });
    });
    return request;
  },
};

try {
  (globalThis as any).window = { indexedDB };
  (globalThis as any).indexedDB = indexedDB;
  batches = [batch];
  const first = actions.resumeStudioTranslationUpload(batch);
  const duplicate = actions.resumeStudioTranslationUpload(batch);
  assert.equal(first, duplicate, "an in-flight upload is reused");
  await first;
  assert.deepEqual(batches, [], "an empty, unpersisted batch is discarded");
  assert.equal(studioUploadRequestsRef.current.has(batch.id), false);
} finally {
  if (originalWindow === undefined) delete (globalThis as any).window;
  else (globalThis as any).window = originalWindow;
  if (originalIndexedDB === undefined) delete (globalThis as any).indexedDB;
  else (globalThis as any).indexedDB = originalIndexedDB;
}

console.log("translation batch upload contracts passed");
