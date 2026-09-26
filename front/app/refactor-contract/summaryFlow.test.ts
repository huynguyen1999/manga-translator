import assert from "node:assert/strict";
import { dismissSummaryJob, summaryJobStageLabel } from "../utils/summaryJobs";

const originalFetch = globalThis.fetch;
const captured: { request?: { url: string; method?: string; headers?: HeadersInit; body?: string } } = {};
globalThis.fetch = (async (input, init) => {
  captured.request = {
    url: String(input),
    method: init?.method,
    headers: init?.headers,
    body: init?.body as string | undefined,
  };
  return { ok: true } as Response;
}) as typeof fetch;

try {
  await dismissSummaryJob({ groupId: "group-1", title: "One Piece" });
  assert.ok(captured.request);
  const request = captured.request;
  assert.equal(new URL(request.url).pathname, "/results/group/summary/dismiss");
  assert.equal(request.method, "POST");
  assert.equal(new Headers(request.headers).get("Content-Type"), "application/json");
  assert.deepEqual(JSON.parse(request.body || "{}"), {
    groupId: "group-1",
    mangaTitle: "One Piece",
  });
  assert.equal(summaryJobStageLabel("ocr"), "Reading OCR");
  assert.equal(summaryJobStageLabel("complete"), "Complete");
} finally {
  globalThis.fetch = originalFetch;
}

console.log("summary flow contracts passed");
