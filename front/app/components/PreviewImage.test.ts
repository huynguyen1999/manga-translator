import assert from "node:assert/strict";
import { countOriginalTextRegions, parseDetectionRegions, parseTextRegions } from "@/utils/textRegions";
import { getConfidenceStyle } from "@/components/PreviewImage";

// Helper functions that mirror PreviewImage calculation logic
export function calculateBubblePercentages(
  box: { x: number; y: number; width: number; height: number },
  naturalSize: { width: number; height: number }
) {
  return {
    leftPct: (box.x / naturalSize.width) * 100,
    topPct: (box.y / naturalSize.height) * 100,
    widthPct: (box.width / naturalSize.width) * 100,
    heightPct: (box.height / naturalSize.height) * 100,
  };
}

export function calculateToolbarPosition(
  imageRect: { left: number; top: number; width: number; height: number } | null
) {
  if (!imageRect) {
    return {
      top: "8px",
      left: "50%",
      transform: "translateX(-50%)",
    };
  }
  return {
    top: `${Math.max(8, imageRect.top + 8)}px`,
    left: `${imageRect.left + imageRect.width / 2}px`,
    transform: "translateX(-50%)",
  };
}

export function calculateCardPlacement(
  selectedBlock: { x: number; y: number; width: number; height: number },
  naturalSize: { width: number; height: number }
) {
  const isNearBottom = (selectedBlock.y + selectedBlock.height) / naturalSize.height > 0.62;
  const rawXPct = ((selectedBlock.x + selectedBlock.width / 2) / naturalSize.width) * 100;
  const clampedXPct = Math.max(20, Math.min(80, rawXPct));

  return {
    isNearBottom,
    clampedXPct,
    verticalPlacement: isNearBottom ? "above" : "below",
  };
}

// Test 1: Bubble coordinate percentage scaling
{
  const natural = { width: 1200, height: 1800 };
  const box = { x: 300, y: 450, width: 240, height: 360 };
  const pcts = calculateBubblePercentages(box, natural);

  assert.equal(pcts.leftPct, 25);
  assert.equal(pcts.topPct, 25);
  assert.equal(pcts.widthPct, 20);
  assert.equal(pcts.heightPct, 20);
}

// Test 2: Toolbar anchoring on top of centered image
{
  // Widescreen container: image centered horizontally with 300px margins on left/right, 40px margin on top
  const centeredImageRect = { left: 300, top: 40, width: 600, height: 900 };
  const pos = calculateToolbarPosition(centeredImageRect);

  assert.equal(pos.top, "48px", "Toolbar should be anchored 8px below top edge of the image");
  assert.equal(pos.left, "600px", "Toolbar should be centered horizontally over the image");
  assert.equal(pos.transform, "translateX(-50%)");
}

// Test 3: Toolbar anchoring when image starts at top = 0
{
  const flushImageRect = { left: 150, top: 0, width: 500, height: 800 };
  const pos = calculateToolbarPosition(flushImageRect);

  assert.equal(pos.top, "8px", "Toolbar should clamp to minimum 8px top");
  assert.equal(pos.left, "400px");
}

// Test 4: Toolbar fallback when image rect is not yet ready
{
  const pos = calculateToolbarPosition(null);
  assert.equal(pos.top, "8px");
  assert.equal(pos.left, "50%");
  assert.equal(pos.transform, "translateX(-50%)");
}

// Test 5: Inspection card placement for top vs bottom bubbles
{
  const natural = { width: 1000, height: 1500 };

  // Bubble near the top-left (e.g. y=100, x=50)
  const topBubble = { x: 50, y: 100, width: 120, height: 200 };
  const topCard = calculateCardPlacement(topBubble, natural);
  assert.equal(topCard.isNearBottom, false);
  assert.equal(topCard.verticalPlacement, "below");
  assert.equal(topCard.clampedXPct, 20, "Should be clamped from raw ~11% to 20% to prevent edge overflow");

  // Bubble near the bottom-right (e.g. y=1100, x=900)
  const bottomBubble = { x: 880, y: 1100, width: 100, height: 200 };
  const bottomCard = calculateCardPlacement(bottomBubble, natural);
  assert.equal(bottomCard.isNearBottom, true);
  assert.equal(bottomCard.verticalPlacement, "above");
  assert.equal(bottomCard.clampedXPct, 80, "Should be clamped from raw ~93% to 80% to prevent edge overflow");
}

console.log("All PreviewImage unit tests passed successfully!");

const parsedRegions = parseTextRegions([
  {
    id: "db-box", x: 738, y: 6, width: 49, height: 79, original_text: "キン",
    layout_segments: [
      { x: 700, y: 5, width: 80, height: 60, text: "First" },
      { x: 790, y: 40, width: 90, height: 70, text: "second" },
    ],
  },
  { id: "db-polygon", lines: [[[10, 20], [40, 20], [40, 60], [10, 60]]] },
]);
assert.deepEqual(parsedRegions.map(({ id, x, y, width, height }) => ({ id, x, y, width, height })), [
  { id: "db-box", x: 738, y: 6, width: 49, height: 79 },
  { id: "db-polygon", x: 10, y: 20, width: 30, height: 40 },
]);
assert.deepEqual(parsedRegions[1].lines, [[[10, 20], [40, 20], [40, 60], [10, 60]]]);
assert.deepEqual(parsedRegions[0].layout_segments?.map(({ text }) => text), ["First", "second"]);
assert.equal(countOriginalTextRegions(parsedRegions), 1, "Only saved detector polygons count as original regions");
console.log("Text-region DB shape test passed successfully!");

assert.deepEqual(parseDetectionRegions([
  { pts: [[81, 855], [112, 855], [112, 1093], [81, 1093]], confidence: 0.94 },
  { lines: [[[118, 855], [148, 855], [148, 1064], [118, 1064]]] },
]), [
  { points: [[81, 855], [112, 855], [112, 1093], [81, 1093]], confidence: 0.94 },
  { points: [[118, 855], [148, 855], [148, 1064], [118, 1064]], confidence: null },
], "Raw detector polygons survive even when OCR omits one and parse confidence");
console.log("Detector-region parsing test passed successfully!");

// Test getConfidenceStyle tiers
assert.equal(getConfidenceStyle(0.95).tier, "high");
assert.equal(getConfidenceStyle(0.95).stroke, "#10b981");
assert.equal(getConfidenceStyle(0.75).tier, "medium");
assert.equal(getConfidenceStyle(0.75).stroke, "#f59e0b");
assert.equal(getConfidenceStyle(0.45).tier, "low");
assert.equal(getConfidenceStyle(0.45).stroke, "#ef4444");
assert.equal(getConfidenceStyle(null).tier, "detected");
assert.equal(getConfidenceStyle(undefined).tier, "detected");
console.log("getConfidenceStyle tests passed successfully!");

