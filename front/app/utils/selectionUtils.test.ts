import assert from "node:assert/strict";
import { computeRangeSelection } from "./selectionUtils";

console.log("Running selectionUtils unit tests...");

const files = ["page_01.png", "page_02.png", "page_03.png", "page_04.png", "page_05.png", "page_06.png"];

// Test 1: Normal click on unselected item toggles it to selected
{
  const res = computeRangeSelection({
    selectedIds: new Set<string>(),
    allIds: files,
    targetId: "page_02.png",
    shiftKey: false,
    anchor: null,
  });
  assert.equal(res.nextSelectedIds.size, 1);
  assert.ok(res.nextSelectedIds.has("page_02.png"));
  assert.deepEqual(res.nextAnchor, { id: "page_02.png", wasSelected: true });
}

// Test 2: Normal click on already selected item toggles it to deselected
{
  const res = computeRangeSelection({
    selectedIds: new Set<string>(["page_02.png"]),
    allIds: files,
    targetId: "page_02.png",
    shiftKey: false,
    anchor: null,
  });
  assert.equal(res.nextSelectedIds.size, 0);
  assert.deepEqual(res.nextAnchor, { id: "page_02.png", wasSelected: false });
}

// Test 3: Shift + click forward range selection
{
  // User selected page_02 (anchor)
  const anchor = { id: "page_02.png", wasSelected: true };
  const res = computeRangeSelection({
    selectedIds: new Set<string>(["page_02.png"]),
    allIds: files,
    targetId: "page_05.png",
    shiftKey: true,
    anchor,
  });
  // Should select page_02, page_03, page_04, page_05
  assert.equal(res.nextSelectedIds.size, 4);
  assert.ok(res.nextSelectedIds.has("page_02.png"));
  assert.ok(res.nextSelectedIds.has("page_03.png"));
  assert.ok(res.nextSelectedIds.has("page_04.png"));
  assert.ok(res.nextSelectedIds.has("page_05.png"));
  assert.ok(!res.nextSelectedIds.has("page_01.png"));
  assert.ok(!res.nextSelectedIds.has("page_06.png"));
  assert.deepEqual(res.nextAnchor, { id: "page_05.png", wasSelected: true });
}

// Test 4: Shift + click backward range selection
{
  // User selected page_05 (anchor)
  const anchor = { id: "page_05.png", wasSelected: true };
  const res = computeRangeSelection({
    selectedIds: new Set<string>(["page_05.png"]),
    allIds: files,
    targetId: "page_02.png",
    shiftKey: true,
    anchor,
  });
  // Should select page_02, page_03, page_04, page_05
  assert.equal(res.nextSelectedIds.size, 4);
  assert.ok(res.nextSelectedIds.has("page_02.png"));
  assert.ok(res.nextSelectedIds.has("page_03.png"));
  assert.ok(res.nextSelectedIds.has("page_04.png"));
  assert.ok(res.nextSelectedIds.has("page_05.png"));
  assert.deepEqual(res.nextAnchor, { id: "page_02.png", wasSelected: true });
}

// Test 5: Shift + click range deselection (anchor was deselected)
{
  // All pages initially selected, user unchecks page_02 (anchor: wasSelected: false)
  const allSelected = new Set(files);
  allSelected.delete("page_02.png");
  const anchor = { id: "page_02.png", wasSelected: false };

  const res = computeRangeSelection({
    selectedIds: allSelected,
    allIds: files,
    targetId: "page_04.png",
    shiftKey: true,
    anchor,
  });
  // page_02, page_03, page_04 should be deselected. page_01, page_05, page_06 remain selected.
  assert.ok(!res.nextSelectedIds.has("page_02.png"));
  assert.ok(!res.nextSelectedIds.has("page_03.png"));
  assert.ok(!res.nextSelectedIds.has("page_04.png"));
  assert.ok(res.nextSelectedIds.has("page_01.png"));
  assert.ok(res.nextSelectedIds.has("page_05.png"));
  assert.ok(res.nextSelectedIds.has("page_06.png"));
  assert.equal(res.nextSelectedIds.size, 3);
  assert.deepEqual(res.nextAnchor, { id: "page_04.png", wasSelected: false });
}

// Test 6: Shift + click with no anchor falls back to single toggle
{
  const res = computeRangeSelection({
    selectedIds: new Set<string>(),
    allIds: files,
    targetId: "page_03.png",
    shiftKey: true,
    anchor: null,
  });
  assert.equal(res.nextSelectedIds.size, 1);
  assert.ok(res.nextSelectedIds.has("page_03.png"));
  assert.deepEqual(res.nextAnchor, { id: "page_03.png", wasSelected: true });
}

// Test 7: Shift + click with deleted/missing anchor falls back to single toggle
{
  const res = computeRangeSelection({
    selectedIds: new Set<string>(["page_01.png"]),
    allIds: files,
    targetId: "page_03.png",
    shiftKey: true,
    anchor: { id: "deleted_file.png", wasSelected: true },
  });
  assert.equal(res.nextSelectedIds.size, 2);
  assert.ok(res.nextSelectedIds.has("page_01.png"));
  assert.ok(res.nextSelectedIds.has("page_03.png"));
  assert.deepEqual(res.nextAnchor, { id: "page_03.png", wasSelected: true });
}

// Test 8: Respects isItemSelectable (e.g. skips actively processing items)
{
  const anchor = { id: "page_01.png", wasSelected: true };
  const res = computeRangeSelection({
    selectedIds: new Set<string>(["page_01.png"]),
    allIds: files,
    targetId: "page_04.png",
    shiftKey: true,
    anchor,
    isItemSelectable: (id) => id !== "page_03.png", // page_03 is processing / disabled
  });
  // page_01, page_02, page_04 selected, but page_03 was skipped
  assert.ok(res.nextSelectedIds.has("page_01.png"));
  assert.ok(res.nextSelectedIds.has("page_02.png"));
  assert.ok(!res.nextSelectedIds.has("page_03.png"));
  assert.ok(res.nextSelectedIds.has("page_04.png"));
  assert.equal(res.nextSelectedIds.size, 3);
}

// Test 9: Chained range selections
{
  // Step 1: Click page_01
  const step1 = computeRangeSelection({
    selectedIds: new Set<string>(),
    allIds: files,
    targetId: "page_01.png",
    shiftKey: false,
    anchor: null,
  });
  // Step 2: Shift+click page_03
  const step2 = computeRangeSelection({
    selectedIds: step1.nextSelectedIds,
    allIds: files,
    targetId: "page_03.png",
    shiftKey: true,
    anchor: step1.nextAnchor,
  });
  // Step 3: Shift+click page_05
  const step3 = computeRangeSelection({
    selectedIds: step2.nextSelectedIds,
    allIds: files,
    targetId: "page_05.png",
    shiftKey: true,
    anchor: step2.nextAnchor,
  });
  assert.equal(step3.nextSelectedIds.size, 5);
  for (const p of ["page_01.png", "page_02.png", "page_03.png", "page_04.png", "page_05.png"]) {
    assert.ok(step3.nextSelectedIds.has(p));
  }
}

console.log("All selectionUtils tests passed successfully!");
