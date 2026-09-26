import assert from "node:assert/strict";
import React from "react";
import type { SetStateAction } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { SummaryJob } from "@/types";
import { useSummaryJobActions } from "./useSummaryJobActions";

const updates: SetStateAction<SummaryJob[]>[] = [];
let actions!: ReturnType<typeof useSummaryJobActions>;

function Harness() {
  actions = useSummaryJobActions((update) => {
    updates.push(update);
  });
  return null;
}

renderToStaticMarkup(React.createElement(Harness));

const originalFetch = globalThis.fetch;
const paths: string[] = [];
globalThis.fetch = (async (input) => {
  paths.push(new URL(String(input), "http://localhost").pathname);
  return { ok: true } as Response;
}) as typeof fetch;

const job = {
  id: "summary-1",
  groupId: "group-1",
  title: "One Piece",
  status: "generating",
  jobStage: "complete",
  jobProgress: 42,
  jobError: "old error",
  jobExtractionRequired: false,
} as SummaryJob;

const applyUpdate = (current: SummaryJob[]) => {
  const update = updates.shift();
  assert.ok(update);
  return typeof update === "function" ? update(current) : update;
};

try {
  await actions.handleDismissSummaryJob(job);
  assert.equal(paths.pop(), "/results/group/summary/dismiss");
  assert.deepEqual(applyUpdate([job]), []);

  await actions.handleRetrySummaryJob(job, "test-model");
  assert.equal(paths.pop(), "/results/group/summary");
  const retried = applyUpdate([job])[0];
  assert.equal(retried.status, "generating");
  assert.equal(retried.jobStage, "summarizing");
  assert.equal(retried.jobProgress, 0);
  assert.equal(retried.jobError, null);
  assert.equal(retried.jobExtractionRequired, false);

  await actions.handleRetrySummaryJob(job, "test-model", true);
  assert.equal(paths.pop(), "/results/group/summary");
  const refreshed = applyUpdate([job])[0];
  assert.equal(refreshed.jobStage, "detecting");
  assert.equal(refreshed.jobExtractionRequired, true);

  await actions.handlePauseSummaryJob(job);
  assert.equal(paths.pop(), "/results/group/summary/pause");
  assert.equal(applyUpdate([job])[0].status, "paused");

  await actions.handleResumeSummaryJob(job);
  assert.equal(paths.pop(), "/results/group/summary/resume");
  assert.equal(applyUpdate([job])[0].status, "generating");

  await actions.handleStopSummaryJob(job);
  assert.equal(paths.pop(), "/results/group/summary/stop");
  assert.deepEqual(applyUpdate([job]), []);
} finally {
  globalThis.fetch = originalFetch;
}

console.log("summary job action contracts passed");
