import assert from "node:assert/strict";
import {
  addStorySplit,
  addStorySplitAt,
  buildStoryPlan,
  mergeStoryPlan,
  removeStorySplit,
  removeStorySplitAt,
  updateStoryBoundary,
  validateStoryPlan,
} from "./storyPlan";

const page = (id: string, dropOrder: number, archiveId: string, archiveName: string) => ({
  id,
  file: new File(["page"], `${id}.jpg`, { type: "image/jpeg" }),
  sourcePath: `${id}.jpg`,
  addedAt: 0,
  dropOrder,
  archiveId,
  archiveName,
});

const plan = buildStoryPlan([page("a1", 1, "a", "a.cbz"), page("a2", 2, "a", "a.cbz"), page("b1", 3, "b", "b.cbz")]);
assert.deepEqual(plan.archives.map((archive) => archive.pageCount), [2, 1]);
assert.deepEqual(plan.segments.map((segment) => [segment.startPage, segment.endPage]), [[1, 2], [3, 3]]);

let editable = buildStoryPlan([page("a1", 1, "a", "a.cbz"), page("b1", 2, "b", "b.cbz")]);
editable = addStorySplit(mergeStoryPlan(editable));
editable = updateStoryBoundary(editable, 0, 1);
assert.equal(validateStoryPlan(editable, 2).valid, true);
assert.equal(removeStorySplit(editable, 1).segments.length, 1);

// Test addStorySplitAt & removeStorySplitAt on a larger sequence
const tenPages = Array.from({ length: 10 }, (_, i) => page(`p${i + 1}`, i + 1, "arch", "arch.zip"));
let tenPlan = buildStoryPlan(tenPages);
assert.equal(tenPlan.segments.length, 1);
assert.deepEqual([tenPlan.segments[0].startPage, tenPlan.segments[0].endPage], [1, 10]);

// Add split after page 4
tenPlan = addStorySplitAt(tenPlan, 4);
assert.equal(tenPlan.segments.length, 2);
assert.deepEqual(tenPlan.segments.map(s => [s.startPage, s.endPage]), [[1, 4], [5, 10]]);

// Add split after page 7
tenPlan = addStorySplitAt(tenPlan, 7);
assert.equal(tenPlan.segments.length, 3);
assert.deepEqual(tenPlan.segments.map(s => [s.startPage, s.endPage]), [[1, 4], [5, 7], [8, 10]]);

// Remove split after page 4
tenPlan = removeStorySplitAt(tenPlan, 4);
assert.equal(tenPlan.segments.length, 2);
assert.deepEqual(tenPlan.segments.map(s => [s.startPage, s.endPage]), [[1, 7], [8, 10]]);

// Remove first segment (index 0)
tenPlan = removeStorySplit(tenPlan, 0);
assert.equal(tenPlan.segments.length, 1);
assert.deepEqual([tenPlan.segments[0].startPage, tenPlan.segments[0].endPage], [1, 10]);

console.log("story plan checks passed");
