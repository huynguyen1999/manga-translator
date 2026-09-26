import assert from "node:assert/strict";
import React from "react";
import type { Dispatch, SetStateAction } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { MangaSummary } from "@/types";
import type { MangaSummaryModalState } from "./MangaSummaryModal";
import { useMangaSummaryActions, type SummaryAvailabilityState } from "./useMangaSummaryActions";

let actions!: ReturnType<typeof useMangaSummaryActions>;
let summaryState: MangaSummaryModalState | null = null;
let summaryAvailability: { title: string; state: SummaryAvailabilityState } | null = null;
let summarizingTitle: string | null = null;
let summaryCopied = false;
const update = <T,>(action: SetStateAction<T>, current: T): T =>
  typeof action === "function" ? (action as (previous: T) => T)(current) : action;
const setSummaryState: Dispatch<SetStateAction<MangaSummaryModalState | null>> = (action) => {
  summaryState = update(action, summaryState);
};
const setSummaryAvailability: Dispatch<SetStateAction<{ title: string; state: SummaryAvailabilityState } | null>> = (action) => {
  summaryAvailability = update(action, summaryAvailability);
};
const setSummarizingTitle: Dispatch<SetStateAction<string | null>> = (action) => {
  summarizingTitle = update(action, summarizingTitle);
};
const setSummaryCopied: Dispatch<SetStateAction<boolean>> = (action) => {
  summaryCopied = update(action, summaryCopied);
};

function Harness() {
  actions = useMangaSummaryActions({
    summaryModel: "test-model",
    mangaGroups: [{ id: "group-1", title: "One Piece" }],
    summaryState,
    setSummaryState,
    summaryAvailability,
    setSummaryAvailability,
    summarizingTitle,
    setSummarizingTitle,
    setSummaryCopied,
  });
  return null;
}

const renderHarness = () => renderToStaticMarkup(React.createElement(Harness));
renderHarness();

const requests: Array<{ url: string; method?: string; body?: string }> = [];
const originalFetch = globalThis.fetch;
globalThis.fetch = (async (input, init) => {
  requests.push({ url: String(input), method: init?.method, body: init?.body as string | undefined });
  return {
    ok: true,
    json: async () => ({
      groupId: "group-1",
      mangaTitle: "One Piece",
      summary: "A crew sets sail.",
      stale: false,
      pageCount: 1,
      textPageCount: 1,
    } satisfies MangaSummary),
  } as Response;
}) as typeof fetch;

try {
  await actions.handleViewSummary("One Piece");
  const viewRequest = new URL(requests[0].url, "http://studio.test");
  assert.equal(viewRequest.pathname, "/results/group/summary");
  assert.equal(viewRequest.searchParams.get("groupId"), "group-1");
  assert.equal(viewRequest.searchParams.get("title"), "One Piece");

  await actions.handleSummarize("One Piece", true, true);
  const generateRequest = requests[1];
  assert.equal(new URL(generateRequest.url, "http://studio.test").pathname, "/results/group/summary");
  assert.equal(generateRequest.method, "POST");
  assert.deepEqual(JSON.parse(generateRequest.body || "{}"), {
    groupId: "group-1",
    mangaTitle: "One Piece",
    summaryModel: "test-model",
    regenerate: true,
    refreshText: true,
  });
  assert.equal((summaryAvailability as { title: string; state: SummaryAvailabilityState } | null)?.state, "summarized");
  assert.equal(summaryCopied, false);

  renderHarness();
  await actions.handlePauseSummaryJob();
  assert.equal(new URL(requests[2].url, "http://studio.test").pathname, "/results/group/summary/pause");
  assert.equal((summaryState as MangaSummaryModalState | null)?.data?.jobStatus, "paused");
  assert.equal((summaryAvailability as { title: string; state: SummaryAvailabilityState } | null)?.state, "paused");

  await actions.handleResumeSummaryJob();
  assert.equal(new URL(requests[3].url, "http://studio.test").pathname, "/results/group/summary/resume");
  assert.equal((summaryState as MangaSummaryModalState | null)?.data?.jobStatus, "generating");
  assert.equal((summaryAvailability as { title: string; state: SummaryAvailabilityState } | null)?.state, "generating");

  await actions.handleStopSummaryJob();
  assert.equal(new URL(requests[4].url, "http://studio.test").pathname, "/results/group/summary/stop");
  assert.equal(summaryState, null);
  assert.equal((summaryAvailability as { title: string; state: SummaryAvailabilityState } | null)?.state, "not-summarized");
} finally {
  globalThis.fetch = originalFetch;
}

console.log("manga summary action contracts passed");
