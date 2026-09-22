import assert from "node:assert/strict";
import { reorderSeriesMembers } from "./series";

const members = [{ id: "one" }, { id: "two" }, { id: "three" }];

assert.deepEqual(reorderSeriesMembers(members, "one", "three"), [
  { id: "two" },
  { id: "one" },
  { id: "three" },
]);
assert.deepEqual(reorderSeriesMembers(members, "three", "one"), [
  { id: "three" },
  { id: "one" },
  { id: "two" },
]);
assert.deepEqual(reorderSeriesMembers(members, "missing", "one"), members);

// Test empty and single element array reordering
assert.deepEqual(reorderSeriesMembers([], "one", "two"), []);
assert.deepEqual(reorderSeriesMembers([{ id: "single" }], "single", "single"), [{ id: "single" }]);

// Test move backward and forward
assert.deepEqual(reorderSeriesMembers(members, "three", "two"), [
  { id: "one" },
  { id: "three" },
  { id: "two" },
]);

console.log("series reorder checks passed");

