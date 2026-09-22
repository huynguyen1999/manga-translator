import assert from "node:assert/strict";
import {
  getFitZoom,
  normalizeBlockDimensions,
  estimateFitFontSize,
  wrapTextLines,
} from "./mangaEditorGeometry";


// 1. Zoom fitting
assert.equal(getFitZoom(1000, 1000, 500, 500), 1);
assert.equal(getFitZoom(664, 864, 1000, 1400), 0.5714285714285714);
assert.equal(getFitZoom(0, 0, 1000, 1400), 0.05);

// 2. Normalizing narrow vertical OCR boxes into speech bubbles
const narrowBox = { x: 1056, y: 531, width: 39, height: 119, direction: "h" as const };
const normalized = normalizeBlockDimensions(narrowBox, 1280, 1807);
assert.ok(normalized.width >= 100, "Narrow box width should expand for horizontal text");
assert.ok(normalized.x < 1056, "Expanded box should center around original x");
assert.ok(normalized.x + normalized.width <= 1280, "Box must stay within canvas bounds");

const edgeBounds = normalizeBlockDimensions(
  { x: 1230, y: 1740, width: 200, height: 120, direction: "h" as const },
  1280,
  1807,
);
assert.ok(edgeBounds.x >= 0 && edgeBounds.y >= 0);
assert.ok(edgeBounds.x + edgeBounds.width <= 1280);
assert.ok(edgeBounds.y + edgeBounds.height <= 1807);

// 3. Estimating font size to avoid overflow
// Bubble 1 text: 52 chars in 180x200 box
const longText = "It's even rougher than when you came here yesterday.";
const fittedSize = estimateFitFontSize(longText, 180, 200, 51);
assert.ok(fittedSize <= 24, `Font size should scale down from 51 to fit without overflow, got ${fittedSize}`);
assert.ok(fittedSize >= 12, `Font size should remain readable, got ${fittedSize}`);


// Short text in wide box
const shortText = "Food, food...";
const shortFit = estimateFitFontSize(shortText, 200, 150, 48);
assert.ok(shortFit >= 24, `Short text should keep a good comic size, got ${shortFit}`);

const measure = (value: string) => value.length * 10;
const wrapped = wrapTextLines("Sales Department: New Graduate Hire", measure, 90);
assert.equal(wrapped[0], "Sales");
assert.ok(wrapped.some((line) => line.endsWith("-")));
assert.ok(wrapped.includes("Graduate"));
assert.ok(wrapTextLines("Pneumonoultramicroscopicsilicovolcanoconiosis", measure, 50).some((line) => line.endsWith("-")));
assert.deepEqual(wrapTextLines("First line\nSecond line", measure, 200), ["First line", "Second line"]);

for (const phrase of [
  "Sales Department: New Graduate Hire",
  "Oh, his laughing",
  "I'm really drunk...",
  "My body... is tingling...",
  "What is hidden behind?",
  "I don't think I'd do that...",
]) {
  const lines = wrapTextLines(phrase, measure, 90);
  assert.ok(lines.length > 0 && lines.every((line) => line.length > 0), `Phrase should wrap: ${phrase}`);
}

console.log("manga editor geometry checks passed successfully");
