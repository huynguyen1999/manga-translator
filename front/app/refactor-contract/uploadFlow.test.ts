import assert from "node:assert/strict";
import type { TranslationBatch } from "@/types";
import { submitServerBatch, type ServerBatch } from "../utils/serverBatches";

const response: ServerBatch = {
  id: "upload/batch",
  title: "Chapter 1",
  mangaTitle: "Chapter 1",
  addedAt: 1,
  settings: {} as ServerBatch["settings"],
  status: "waiting",
  dismissed: false,
  totalItems: 2,
  completedCount: 1,
  queuedCount: 1,
  processingCount: 0,
  failedCount: 0,
  needsReviewCount: 0,
  items: [],
};

let requestMethod = "";
let requestUrl = "";
const captured: { body?: FormData } = {};

class UploadRequest {
  status = 200;
  responseText = JSON.stringify(response);
  upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;

  open(method: string, url: string) {
    requestMethod = method;
    requestUrl = url;
  }

  send(body: FormData) {
    captured.body = body;
    this.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 4 } as ProgressEvent);
    this.onload?.();
  }
}

const originalXMLHttpRequest = globalThis.XMLHttpRequest;
globalThis.XMLHttpRequest = UploadRequest as unknown as typeof XMLHttpRequest;

try {
  const now = new Date("2026-09-25T00:00:00Z");
  const batch: TranslationBatch = {
    id: "upload/batch",
    addedAt: now,
    mangaTitle: "  Chapter 1  ",
    settings: {} as TranslationBatch["settings"],
    items: [
      {
        id: "pending-page",
        file: new File(["pending"], "pending.png", { type: "image/png" }),
        addedAt: now,
        status: "queued",
      },
      {
        id: "finished-page",
        file: new File(["already uploaded"], "finished.png", { type: "image/png" }),
        addedAt: now,
        status: "finished",
        folder: "result-folder",
      },
    ],
    totalItems: 2,
    completedCount: 1,
    status: "uploading",
  };
  const progress: number[] = [];

  const submitted = await submitServerBatch(batch, (value) => progress.push(value));

  assert.equal(submitted.id, response.id);
  assert.equal(requestMethod, "PUT");
  assert.equal(new URL(requestUrl).pathname, "/batches/upload%2Fbatch");
  assert.deepEqual(progress, [25]);
  assert.ok(captured.body);
  const form = captured.body;
  const manifestPart = form.get("manifest");
  assert.ok(manifestPart instanceof Blob);
  const manifest = JSON.parse(await manifestPart.text());
  assert.equal(manifest.mangaTitle, "Chapter 1");
  assert.equal(manifest.items[0].status, "queued");
  assert.equal(manifest.items[1].status, "completed");
  assert.ok(form.get("pending-page") instanceof File);
  assert.equal(form.get("finished-page"), null);
} finally {
  globalThis.XMLHttpRequest = originalXMLHttpRequest;
}

console.log("upload flow contracts passed");
