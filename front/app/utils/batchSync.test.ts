import assert from "node:assert/strict";
import type { ServerBatch } from "./serverBatches";
import { mergeServerBatches, toTranslationBatch } from "./serverBatches";

const serverBatch = (
  dismissed: boolean,
  translator: ServerBatch["settings"]["translator"] = "deepseek",
): ServerBatch => ({
  id: "batch-1",
  title: "Completed batch",
  mangaTitle: "Completed batch",
  addedAt: 1,
  settings: { translator } as ServerBatch["settings"],
  status: "completed",
  dismissed,
  totalItems: 1,
  completedCount: 1,
  queuedCount: 0,
  processingCount: 0,
  failedCount: 0,
  needsReviewCount: 0,
  items: [],
});

const current = mergeServerBatches([], [serverBatch(false)], new Set(["batch-1"]));

assert.equal(current.length, 1);
assert.equal(current[0].dismissed, true);

const confirmed = mergeServerBatches([], [serverBatch(true)], new Set());
assert.equal(confirmed[0].dismissed, true);

const pendingTranslator = mergeServerBatches(
  [],
  [serverBatch(false, "deepseek")],
  new Set(),
  new Map([["batch-1", "sugoi"]]),
);
assert.equal(pendingTranslator[0].settings.translator, "sugoi");

console.log("batch synchronization tests passed successfully!");

const { items: _summaryItems, ...summary } = { ...serverBatch(false), updatedAt: 2 };
const detailed = toTranslationBatch({
  ...serverBatch(false),
  updatedAt: 1,
  items: [{
    id: "page-1",
    name: "page-1.png",
    status: "completed",
  }],
});
const mergedDetailed = mergeServerBatches([detailed], [summary], new Set());
assert.equal(mergedDetailed[0].detailsLoaded, true);
assert.equal(mergedDetailed[0].items[0].id, "page-1");
assert.equal(mergedDetailed[0].updatedAt?.getTime(), 2);

const mappedSummary = toTranslationBatch(summary);
assert.equal(mappedSummary.detailsLoaded, false);
assert.equal(mappedSummary.items.length, 0);

const finishedServerBatch = toTranslationBatch({
  id: "batch-2",
  title: "Batch Two",
  mangaTitle: "Batch Two",
  status: "completed",
  addedAt: 1,
  dismissed: false,
  totalItems: 2,
  completedCount: 2,
  queuedCount: 0,
  processingCount: 0,
  failedCount: 0,
  needsReviewCount: 0,
  settings: { translator: "deepseek" } as ServerBatch["settings"],
  items: [
    {
      id: "page-1",
      name: "01.png",
      status: "completed",
      resultFolder: "2026-09-20_001",
      resultUrl: "/result/2026-09-20_001/final.png",
      inputUrl: "/api/batches/batch-2/items/page-1/input",
    },
    {
      id: "page-2",
      name: "02.png",
      status: "completed",
      resultFolder: "2026-09-20_002",
      resultUrl: "/result/2026-09-20_002/final.jpg",
      inputUrl: "/api/batches/batch-2/items/page-2/input",
    },
  ],
});

assert.equal(finishedServerBatch.detailsLoaded, true);
assert.equal(finishedServerBatch.items.length, 2);
assert.equal(finishedServerBatch.items[0].status, "finished");
assert.equal(finishedServerBatch.items[0].folder, "2026-09-20_001");
assert.equal(finishedServerBatch.items[0].result, "/result/2026-09-20_001/final.png");
assert.equal(finishedServerBatch.items[1].status, "finished");
assert.equal(finishedServerBatch.items[1].folder, "2026-09-20_002");
assert.equal(finishedServerBatch.items[1].result, "/result/2026-09-20_002/final.jpg");

console.log("batch detail merge tests passed successfully!");
