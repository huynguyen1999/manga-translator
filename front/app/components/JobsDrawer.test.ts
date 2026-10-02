import assert from "node:assert/strict";
import {
  batchSection,
  getJobCreationTimestamp,
  getJobCompletionTimestamp,
  sectionLabels,
  shouldCloseJobsDrawer,
  sortJobEntries,
  summarySection,
  mangaImportJobSection,
  type JobEntry,
} from "@/components/JobsDrawer";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MangaImportJobRow } from "./MangaImportJobRow";
import type { MangaImportJob } from "@/features/upload/mangaImportJobs";
import type { SummaryJob, TranslationBatch } from "@/types";

const insideViewer = {} as EventTarget;
const insideDrawer = {} as EventTarget;
const drawer = {
  contains: (target: EventTarget) => target === insideDrawer,
} as unknown as HTMLElement;

assert.equal(shouldCloseJobsDrawer(insideDrawer, drawer), true);
assert.equal(shouldCloseJobsDrawer(insideViewer, drawer), false);
assert.equal(shouldCloseJobsDrawer(null, drawer), false);

// Section grouping tests
assert.equal(summarySection({ status: "error" } as SummaryJob), "attention");
assert.equal(summarySection({ status: "queued" } as SummaryJob), "queued");
assert.equal(summarySection({ status: "paused" } as SummaryJob), "queued");
assert.equal(summarySection({ status: "generating" } as SummaryJob), "active");
assert.equal(summarySection({ status: "ready" } as SummaryJob), "completed");
assert.equal(mangaImportJobSection({ status: "failed" } as MangaImportJob), "attention");
assert.equal(mangaImportJobSection({ status: "queued" } as MangaImportJob), "queued");
assert.equal(mangaImportJobSection({ status: "processing" } as MangaImportJob), "active");
assert.equal(mangaImportJobSection({ status: "completed" } as MangaImportJob), "completed");

assert.equal(batchSection({ status: "error", failedCount: 0 } as TranslationBatch), "attention");
assert.equal(batchSection({ status: "completed", failedCount: 2 } as TranslationBatch), "attention");
assert.equal(batchSection({ status: "completed", failedCount: 0 } as TranslationBatch), "completed");
assert.equal(batchSection({ status: "waiting" } as TranslationBatch), "queued");
assert.equal(batchSection({ status: "paused" } as TranslationBatch), "queued");
assert.equal(batchSection({ status: "processing" } as TranslationBatch), "active");

assert.equal(sectionLabels.attention, "Needs attention");
assert.equal(sectionLabels.active, "Active");
assert.equal(sectionLabels.queued, "Queued / Paused");
assert.equal(sectionLabels.completed, "Completed");

// Test filtering for queued batches and summaries
const testBatches: TranslationBatch[] = [
  { id: "b1", status: "waiting", items: [], dismissed: false } as unknown as TranslationBatch,
  { id: "b2", status: "paused", items: [], dismissed: false } as unknown as TranslationBatch,
  { id: "b3", status: "processing", items: [], dismissed: false } as unknown as TranslationBatch,
  { id: "b4", status: "completed", failedCount: 0, items: [], dismissed: false } as unknown as TranslationBatch,
  { id: "b5", status: "waiting", items: [], dismissed: true } as unknown as TranslationBatch,
];
const testSummaries: SummaryJob[] = [
  { id: "s1", status: "queued", title: "Manga 1" } as SummaryJob,
  { id: "s2", status: "paused", title: "Manga 2" } as SummaryJob,
  { id: "s3", status: "generating", title: "Manga 3" } as SummaryJob,
  { id: "s4", status: "ready", title: "Manga 4" } as SummaryJob,
];

const queuedBatches = testBatches.filter((batch) => !batch.dismissed && batchSection(batch) === "queued");
const queuedSummaries = testSummaries.filter((job) => summarySection(job) === "queued");

assert.equal(queuedBatches.length, 2);
assert.deepEqual(queuedBatches.map((b) => b.id), ["b1", "b2"]);
assert.equal(queuedSummaries.length, 2);
assert.deepEqual(queuedSummaries.map((s) => s.id), ["s1", "s2"]);

// Test stable active ordering between summary and translation jobs
const summaryJob: SummaryJob = {
  id: "summary-1",
  kind: "summary",
  title: "Manga Summary",
  status: "generating",
  createdAt: "2026-09-29T10:05:00.000Z",
  updatedAt: "2026-09-29T10:05:10.000Z",
};

const translationBatch: TranslationBatch = {
  id: "batch-1",
  mangaTitle: "Manga Translation",
  status: "processing",
  addedAt: new Date("2026-09-29T10:00:00.000Z"),
  updatedAt: new Date("2026-09-29T10:05:05.000Z"),
  items: [],
  totalItems: 5,
  completedCount: 1,
  settings: {} as any,
};

const activeEntries: JobEntry[] = [
  { type: "summary", value: summaryJob },
  { type: "batch", value: translationBatch },
  { type: "manga-import", value: { id: "import-1", title: "Manga Import", status: "processing", createdAt: "2026-09-29T10:02:00.000Z" } },
];

const initialOrder = sortJobEntries(activeEntries, "active");
// Summary was created at 10:05:00, Batch at 10:00:00 -> Summary first, Batch second
assert.deepEqual(initialOrder.map((e) => e.value.id), ["summary-1", "import-1", "batch-1"]);

// Progress tick on translation batch updates its updatedAt to 10:06:00
const updatedBatch: TranslationBatch = {
  ...translationBatch,
  updatedAt: new Date("2026-09-29T10:06:00.000Z"),
};
const orderAfterBatchProgress = sortJobEntries(
  [{ type: "summary", value: summaryJob }, { type: "batch", value: updatedBatch }],
  "active",
);
// Order MUST remain stable: summary-1 first, batch-1 second (no jumping!)
assert.deepEqual(orderAfterBatchProgress.map((e) => e.value.id), ["summary-1", "batch-1"]);

// Progress tick on summary job updates its updatedAt to 10:07:00
const updatedSummary: SummaryJob = {
  ...summaryJob,
  updatedAt: "2026-09-29T10:07:00.000Z",
};
const orderAfterSummaryProgress = sortJobEntries(
  [{ type: "summary", value: updatedSummary }, { type: "batch", value: updatedBatch }],
  "active",
);
// Order MUST remain stable: summary-1 first, batch-1 second (no jumping!)
assert.deepEqual(orderAfterSummaryProgress.map((e) => e.value.id), ["summary-1", "batch-1"]);

// Priority batch takes precedence over non-priority entries
const priorityBatch: TranslationBatch = {
  ...translationBatch,
  id: "batch-priority",
  priority: true,
};
const orderWithPriority = sortJobEntries(
  [{ type: "summary", value: summaryJob }, { type: "batch", value: priorityBatch }],
  "active",
);
assert.deepEqual(orderWithPriority.map((e) => e.value.id), ["batch-priority", "summary-1"]);

// Completed section sorts by completion/updated timestamp
const completedSummary: SummaryJob = {
  id: "summary-comp",
  kind: "summary",
  title: "Summary Comp",
  status: "ready",
  createdAt: "2026-09-29T09:00:00.000Z",
  updatedAt: "2026-09-29T11:00:00.000Z",
};
const completedBatch: TranslationBatch = {
  id: "batch-comp",
  mangaTitle: "Batch Comp",
  status: "completed",
  addedAt: new Date("2026-09-29T09:30:00.000Z"),
  updatedAt: new Date("2026-09-29T10:30:00.000Z"),
  items: [],
  totalItems: 5,
  completedCount: 5,
  settings: {} as any,
};
const completedOrder = sortJobEntries(
  [{ type: "batch", value: completedBatch }, { type: "summary", value: completedSummary }],
  "completed",
);
assert.deepEqual(completedOrder.map((e) => e.value.id), ["summary-comp", "batch-comp"]);

const stagedFilesFallback = renderToStaticMarkup(React.createElement(MangaImportJobRow, {
  job: {
    id: "import-files",
    title: "Archive Upload",
    status: "queued",
    createdAt: "2026-09-29T10:00:00.000Z",
    fileCount: 12,
  },
  onRetry: () => {},
  onDismiss: () => {},
  onOpen: () => {},
  isActionPending: () => false,
  runAction: () => {},
}));
assert.match(stagedFilesFallback, /12 staged files/);
assert.doesNotMatch(stagedFilesFallback, /12 pages/);

console.log("JobsDrawer Escape, section grouping, and stable ordering tests passed successfully!");
