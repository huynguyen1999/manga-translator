import assert from "node:assert/strict";
import React from "react";
import type { Dispatch, SetStateAction } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { TranslationBatch, TranslationSettings } from "@/types";
import { useStudioColorActions } from "./useStudioColorActions";

type State = {
  excluded: Set<string>;
  autoDetected: Set<string>;
  batches: TranslationBatch[];
};
const state: State = {
  excluded: new Set(["studio-1"]),
  autoDetected: new Set(),
  batches: [{
    id: "batch-1",
    addedAt: new Date(0),
    mangaTitle: "Title",
    settings: {} as TranslationSettings,
    items: [{
      id: "queue-1",
      file: new File(["page"], "page.png", { type: "image/png" }),
      addedAt: new Date(0),
      status: "queued",
      excludeColor: false,
    }],
    totalItems: 1,
    completedCount: 0,
    status: "waiting",
  }],
};
const update = <T,>(action: SetStateAction<T>, current: T): T =>
  typeof action === "function" ? (action as (previous: T) => T)(current) : action;
const setExcluded: Dispatch<SetStateAction<Set<string>>> = (action) => { state.excluded = update(action, state.excluded); };
const setAutoDetected: Dispatch<SetStateAction<Set<string>>> = (action) => { state.autoDetected = update(action, state.autoDetected); };
const setBatches: Dispatch<SetStateAction<TranslationBatch[]>> = (action) => { state.batches = update(action, state.batches); };
let actions!: ReturnType<typeof useStudioColorActions>;

function Harness() {
  actions = useStudioColorActions({
    selectedFiles: new Set(["studio-1", "studio-2"]),
    translationBatches: state.batches,
    setExcludedColorFiles: setExcluded,
    setAutoDetectedColorFiles: setAutoDetected,
    setTranslationBatches: setBatches,
  });
  return null;
}

renderToStaticMarkup(React.createElement(Harness));
actions.toggleExcludeColorFile("studio-1");
assert.deepEqual([...state.excluded], []);
actions.setExcludeColorForSelected(true);
assert.deepEqual([...state.excluded], ["studio-1", "studio-2"]);
actions.setExcludeColorForSelected(false);
assert.deepEqual([...state.excluded], []);

let request: { url: string; method?: string; body?: string } | undefined;
const originalFetch = globalThis.fetch;
globalThis.fetch = (async (input, init) => {
  request = { url: String(input), method: init?.method, body: init?.body as string | undefined };
  return { ok: true } as Response;
}) as typeof fetch;
try {
  actions.toggleQueueItemColor("batch-1", "queue-1");
  assert.equal(state.batches[0].items[0].excludeColor, true);
  await Promise.resolve();
  assert.ok(request);
  assert.equal(new URL(request.url, "http://studio.test").pathname, "/batches/batch-1/items/queue-1");
  assert.equal(request.method, "PATCH");
  assert.deepEqual(JSON.parse(request.body || "{}"), { excludeColor: true });
  assert.deepEqual([...state.autoDetected], []);
} finally {
  globalThis.fetch = originalFetch;
}

console.log("studio color action contracts passed");
