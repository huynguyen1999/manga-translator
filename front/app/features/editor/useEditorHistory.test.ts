import assert from "node:assert/strict";
import type { EditableTextBlock } from "@/types";
import {
  appendEditorHistory,
  redoEditorHistory,
  undoEditorHistory,
} from "./useEditorHistory";

const block = (id: string): EditableTextBlock => ({
  id,
  x: 0,
  y: 0,
  width: 10,
  height: 10,
  translation: id,
  font_size: 12,
  font_family: "sans-serif",
  fg_color: [0, 0, 0],
  bg_color: [255, 255, 255],
  stroke_width: 0,
  angle: 0,
  direction: "h",
  alignment: "center",
  line_spacing: 1,
  letter_spacing: 0,
  bold: false,
  italic: false,
});

const first = [block("first")];
const second = [block("second")];
const third = [block("third")];

assert.deepEqual(undoEditorHistory([first], second), { current: first, past: [] });
assert.equal(undoEditorHistory([], second), null);
assert.deepEqual(redoEditorHistory([third], second), { current: third, future: [] });
assert.equal(redoEditorHistory([], second), null);
const capped = appendEditorHistory(Array.from({ length: 50 }, () => first), second);
assert.equal(capped.length, 50);
assert.equal(capped[0], first);
assert.equal(capped.at(-1), second);
