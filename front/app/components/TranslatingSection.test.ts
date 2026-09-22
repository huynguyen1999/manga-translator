import assert from "node:assert/strict";
import type { TranslationBatch } from "@/types";
import { canChangeBatchTranslator, sortBatchesLatestFirst } from "./TranslatingSection";
import { getBatchKind } from "@/utils/serverBatches";

const makeBatch = (id: string, addedAt: Date, dismissed = false): TranslationBatch => ({
  id,
  addedAt,
  mangaTitle: `Title ${id}`,
  settings: {} as TranslationBatch["settings"],
  totalItems: 1,
  completedCount: 0,
  dismissed,
  status: "waiting",
  items: [],
});

// 1. Sorts batches so the latest (newest addedAt) is on top (index 0)
const b1 = makeBatch("batch-old", new Date("2026-01-01T10:00:00Z"));
const b2 = makeBatch("batch-mid", new Date("2026-01-01T11:00:00Z"));
const b3 = makeBatch("batch-latest", new Date("2026-01-01T12:00:00Z"));

const sorted = sortBatchesLatestFirst([b1, b2, b3]);
assert.equal(sorted.length, 3);
assert.equal(sorted[0].id, "batch-latest", "The latest batch must be on top");
assert.equal(sorted[1].id, "batch-mid");
assert.equal(sorted[2].id, "batch-old", "The oldest batch must be at the bottom");

// 2. Filters out dismissed batches
const bDismissed = makeBatch("batch-dismissed", new Date("2026-01-01T13:00:00Z"), true);
const filtered = sortBatchesLatestFirst([b1, bDismissed, b3]);
assert.equal(filtered.length, 2);
assert.equal(filtered[0].id, "batch-latest");
assert.equal(filtered[1].id, "batch-old");

// 3. Stable tie-break by ID when addedAt timestamps are equal
const bEqualA = makeBatch("batch-a", new Date("2026-01-01T10:00:00Z"));
const bEqualB = makeBatch("batch-b", new Date("2026-01-01T10:00:00Z"));
const sortedTie = sortBatchesLatestFirst([bEqualA, bEqualB]);
assert.equal(sortedTie[0].id, "batch-b");
// 4. Priority batches are placed ahead of non-priority batches
const bNormal = makeBatch("batch-normal", new Date("2026-01-01T14:00:00Z"));
const bPriority = { ...makeBatch("batch-prio", new Date("2026-01-01T10:00:00Z")), priority: true };
const sortedPrio = sortBatchesLatestFirst([bNormal, bPriority]);
assert.equal(sortedPrio[0].id, "batch-prio");
assert.equal(sortedPrio[1].id, "batch-normal");

// 5. Active batches are placed ahead of completed batches of same priority
const bCompleted = { ...makeBatch("batch-comp", new Date("2026-01-01T15:00:00Z")), status: "completed" as const };
const bActive = { ...makeBatch("batch-active", new Date("2026-01-01T12:00:00Z")), status: "processing" as const };
const sortedActive = sortBatchesLatestFirst([bCompleted, bActive]);
assert.equal(sortedActive[0].id, "batch-active");
assert.equal(sortedActive[1].id, "batch-comp");

// 6. Uploading batches are treated as active and placed ahead of completed batches
const bUploading = { ...makeBatch("batch-uploading", new Date("2026-01-01T12:00:00Z")), status: "uploading" as const };
const sortedUploading = sortBatchesLatestFirst([bCompleted, bUploading]);
assert.equal(sortedUploading[0].id, "batch-uploading");
assert.equal(sortedUploading[1].id, "batch-comp");

console.log("sortBatchesLatestFirst tests passed successfully!");

assert.equal(canChangeBatchTranslator({ ...b1, status: "error", failedCount: 1 }), true);
assert.equal(canChangeBatchTranslator({ ...b1, status: "error", failedCount: 0 }), false);

assert.equal(getBatchKind({ id: "batch-translation", kind: "translation" }), "translation");
assert.equal(getBatchKind({ id: "original-legacy-upload" }), "manga-upload");
assert.equal(getBatchKind({ id: "upload-new-batch", kind: "manga-upload" }), "manga-upload");

// 7. Queued/waiting batches filtering
const bWaiting = makeBatch("batch-waiting", new Date("2026-01-01T10:00:00Z"));
const bPaused = { ...makeBatch("batch-paused", new Date("2026-01-01T11:00:00Z")), status: "paused" as const };
const bProcessing = { ...makeBatch("batch-processing", new Date("2026-01-01T12:00:00Z")), status: "processing" as const };
const allBatches = [bWaiting, bPaused, bProcessing, bCompleted];
const queued = allBatches.filter((b) => b.status === "waiting" || b.status === "paused");
assert.equal(queued.length, 2);
assert.deepEqual(queued.map((b) => b.id), ["batch-waiting", "batch-paused"]);

console.log("canChangeBatchTranslator tests passed successfully!");

// 8. Batch redirect URL generation tests
import { buildMangaDetailIdUrl, mangaIdForTitle } from "@/utils/routeState";

const batchWithGroup = { ...bCompleted, mangaGroupId: "uuid-5678", mangaTitle: "Sakura Garden" };
const batchWithoutGroup = { ...bCompleted, mangaGroupId: null, mangaTitle: "Sakura Garden" };

const targetWithGroup = buildMangaDetailIdUrl(batchWithGroup.mangaGroupId || mangaIdForTitle(batchWithGroup.mangaTitle));
const targetWithoutGroup = buildMangaDetailIdUrl(batchWithoutGroup.mangaGroupId || mangaIdForTitle(batchWithoutGroup.mangaTitle));

assert.equal(targetWithGroup, "/gallery/manga/uuid-5678", "Batch with mangaGroupId must route to /gallery/manga/uuid-5678");
assert.equal(targetWithoutGroup, `/gallery/manga/${mangaIdForTitle("Sakura Garden")}`, "Batch without mangaGroupId must fallback to /gallery/manga/manga-<hash>");

console.log("TranslatingSection redirect URL tests passed successfully!");
