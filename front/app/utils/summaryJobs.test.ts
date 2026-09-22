import assert from "node:assert/strict";
import {
  pauseSummaryJob,
  resumeSummaryJob,
  retrySummaryJob,
  stopSummaryJob,
  summaryJobProgress,
} from "./summaryJobs";

assert.equal(summaryJobProgress({ status: "generating", jobProgress: 42 }), 42);
assert.equal(summaryJobProgress({ status: "ready", jobProgress: null }), 100);
assert.equal(summaryJobProgress({ status: "generating", jobProgress: 140 }), 100);
assert.equal(summaryJobProgress({ status: "error", jobProgress: undefined }), 0);

const originalFetch = globalThis.fetch;
let lastRequest: { url?: string; method?: string; body?: Record<string, unknown> } = {};
globalThis.fetch = (async (input, init) => {
  lastRequest = {
    url: String(input),
    method: init?.method,
    body: init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined,
  };
  return { ok: true } as Response;
}) as typeof fetch;

try {
  await retrySummaryJob({ groupId: "group-1", title: "Series" }, "gemini");
  assert.deepEqual(lastRequest.body, {
    groupId: "group-1",
    mangaTitle: "Series",
    summaryModel: "gemini",
    regenerate: true,
    refreshText: false,
  });

  await retrySummaryJob({ groupId: "group-1", title: "Series" }, "deepseek-flash", true);
  assert.deepEqual(lastRequest.body, {
    groupId: "group-1",
    mangaTitle: "Series",
    summaryModel: "deepseek-flash",
    regenerate: true,
    refreshText: true,
  });

  await pauseSummaryJob({ groupId: "group-1", title: "Series" });
  assert.equal(lastRequest.url?.endsWith("/results/group/summary/pause"), true);
  assert.deepEqual(lastRequest.body, {
    groupId: "group-1",
    mangaTitle: "Series",
  });

  await resumeSummaryJob({ groupId: "group-1", title: "Series" });
  assert.equal(lastRequest.url?.endsWith("/results/group/summary/resume"), true);
  assert.deepEqual(lastRequest.body, {
    groupId: "group-1",
    mangaTitle: "Series",
  });

  await stopSummaryJob({ groupId: "group-1", title: "Series" });
  assert.equal(lastRequest.url?.endsWith("/results/group/summary/stop"), true);
  assert.deepEqual(lastRequest.body, {
    groupId: "group-1",
    mangaTitle: "Series",
  });
} finally {
  globalThis.fetch = originalFetch;
}

console.log("summary job progress and control checks passed");
