import assert from "node:assert/strict";
import type { EditableTextBlock } from "@/types";
import { getEditorDragGeometry } from "./useEditorDrag";

const initial = { x: 100, y: 80, width: 120, height: 60 } as EditableTextBlock;
const move = getEditorDragGeometry({
  type: "move", startX: 10, startY: 10, initialBlock: initial,
}, 25, 20, 1.5);
assert.deepEqual(move, { x: 110, y: 87, layout_bounds: undefined });

const resize = getEditorDragGeometry({
  type: "resize", handle: "nw", startX: 0, startY: 0, initialBlock: initial,
}, -20, -10, 1);
assert.deepEqual(resize, {
  x: 80,
  y: 70,
  width: 140,
  height: 70,
  layout_bounds: undefined,
});

const constrained = getEditorDragGeometry({
  type: "resize", handle: "se", startX: 0, startY: 0, initialBlock: initial,
}, -500, -500, 1);
assert.equal(constrained?.width, 30);
assert.equal(constrained?.height, 20);
