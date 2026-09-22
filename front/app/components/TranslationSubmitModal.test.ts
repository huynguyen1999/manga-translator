import assert from "node:assert/strict";
import {
  addStorySplit,
  addStorySplitAt,
  buildStoryPlan,
  mergeStoryPlan,
  removeStorySplit,
  removeStorySplitAt,
  renameStorySegment,
  updateStoryBoundary,
  validateStoryPlan,
} from "../utils/storyPlan";
import type { StudioFile } from "../types";

const makeFile = (id: string, dropOrder: number, archiveId?: string, archiveName?: string): StudioFile => ({
  id,
  file: new File(["data"], `${id}.jpg`, { type: "image/jpeg" }),
  sourcePath: `${id}.jpg`,
  addedAt: 0,
  dropOrder,
  archiveId,
  archiveName,
});

// Test 1: Single archive initialization and story splitting
{
  const files: StudioFile[] = [
    makeFile("p1", 1, "arch1", "nhentai-678178.zip"),
    makeFile("p2", 2, "arch1", "nhentai-678178.zip"),
    makeFile("p3", 3, "arch1", "nhentai-678178.zip"),
    makeFile("p4", 4, "arch1", "nhentai-678178.zip"),
    makeFile("p5", 5, "arch1", "nhentai-678178.zip"),
    makeFile("p6", 6, "arch1", "nhentai-678178.zip"),
    makeFile("p7", 7, "arch1", "nhentai-678178.zip"),
    makeFile("p8", 8, "arch1", "nhentai-678178.zip"),
  ];

  let plan = buildStoryPlan(files);
  assert.equal(plan.archives.length, 1);
  assert.equal(plan.segments.length, 1);
  assert.deepEqual([plan.segments[0].startPage, plan.segments[0].endPage], [1, 8]);

  // Add split at page 3 (filmstrip inline click)
  plan = addStorySplitAt(plan, 3);
  assert.equal(plan.segments.length, 2);
  assert.deepEqual(plan.segments.map((s) => [s.startPage, s.endPage]), [[1, 3], [4, 8]]);

  // Add split at page 6 (filmstrip inline click)
  plan = addStorySplitAt(plan, 6);
  assert.equal(plan.segments.length, 3);
  assert.deepEqual(plan.segments.map((s) => [s.startPage, s.endPage]), [[1, 3], [4, 6], [7, 8]]);

  // Drag handle between story 1 & story 2 from page 3 to page 2
  plan = updateStoryBoundary(plan, 0, 2);
  assert.deepEqual(plan.segments.map((s) => [s.startPage, s.endPage]), [[1, 2], [3, 6], [7, 8]]);

  // Rename a story
  plan = renameStorySegment(plan, plan.segments[0].id, "Prologue");
  assert.equal(plan.segments[0].label, "Prologue");

  // Remove split at page 6 via handle button
  plan = removeStorySplitAt(plan, 6);
  assert.equal(plan.segments.length, 2);
  assert.deepEqual(plan.segments.map((s) => [s.startPage, s.endPage]), [[1, 2], [3, 8]]);

  // Validation passes
  const validResult = validateStoryPlan(plan, 8);
  assert.equal(validResult.valid, true);
  assert.equal(validResult.errors.length, 0);
}

// Test 2: Multi-archive initialization and merge all pages toggle
{
  const files: StudioFile[] = [
    makeFile("a1", 1, "arc1", "Vol1.cbz"),
    makeFile("a2", 2, "arc1", "Vol1.cbz"),
    makeFile("b1", 3, "arc2", "Vol2.cbz"),
    makeFile("b2", 4, "arc2", "Vol2.cbz"),
  ];

  let plan = buildStoryPlan(files);
  assert.equal(plan.archives.length, 2);
  assert.equal(plan.segments.length, 2);

  // Split within specific archive
  plan = addStorySplit(plan, "arc1");
  assert.equal(plan.segments.length, 3);
  assert.deepEqual(plan.segments.map((s) => [s.startPage, s.endPage]), [[1, 1], [2, 2], [3, 4]]);

  // Toggle merge all pages
  plan = mergeStoryPlan(plan);
  assert.equal(plan.mergeAllPages, true);
  assert.equal(plan.segments.length, 1);
  assert.deepEqual([plan.segments[0].startPage, plan.segments[0].endPage], [1, 4]);

  // Split merged plan
  plan = addStorySplitAt(plan, 2);
  assert.equal(plan.segments.length, 2);
  assert.deepEqual(plan.segments.map((s) => [s.startPage, s.endPage]), [[1, 2], [3, 4]]);

  // Test 3: Cut-off boundary inspection calculations
  const boundaries = plan.segments.slice(0, -1).map((s) => s.endPage);
  assert.deepEqual(boundaries, [2]);
  const cutA = boundaries[0];
  const cutB = cutA + 1;
  assert.equal(cutA, 2);
  assert.equal(cutB, 3);
  const segA = plan.segments.find((s) => s.startPage <= cutA && cutA <= s.endPage);
  const segB = plan.segments.find((s) => s.startPage <= cutB && cutB <= s.endPage);
  assert.equal(segA?.endPage, 2);
  assert.equal(segB?.startPage, 3);
}

console.log("TranslationSubmitModal arrange stories tests passed successfully!");
