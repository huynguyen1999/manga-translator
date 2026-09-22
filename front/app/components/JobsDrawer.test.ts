import assert from "node:assert/strict";
import {
  batchSection,
  sectionLabels,
  shouldCloseJobsDrawer,
  summarySection,
} from "@/components/JobsDrawer";
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

console.log("JobsDrawer Escape and section grouping tests passed successfully!");
