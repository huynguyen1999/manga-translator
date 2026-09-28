import assert from "node:assert/strict";
import { parsePanelDetections, normalizePanelBoxes, type DetectedPanelRegion } from "./panelRegions";

// Test 1: Empty or invalid input
assert.deepEqual(parsePanelDetections(null), []);
assert.deepEqual(parsePanelDetections(undefined), []);
assert.deepEqual(parsePanelDetections("invalid"), []);
assert.deepEqual(parsePanelDetections({}), []);

// Test 2: Standard panel_detections payload
const samplePayload = {
  version: 1,
  coordinate_space: { width: 1000, height: 1500 },
  panels: [
    {
      id: "panel-1",
      order: 1,
      xyxy: [500, 0, 1000, 700],
      polygon: [[500, 0], [1000, 0], [1000, 700], [500, 700]],
      confidence: 0.95,
    },
    {
      id: "panel-2",
      order: 2,
      xyxy: [0, 0, 500, 700],
      polygon: [[0, 0], [500, 0], [500, 700], [0, 700]],
      confidence: 0.92,
    },
    {
      id: "panel-3",
      order: 3,
      xyxy: [0, 700, 1000, 1500],
      confidence: 0.88,
    },
  ],
};

const parsed = parsePanelDetections(samplePayload);
assert.equal(parsed.length, 3);
assert.equal(parsed[0].id, "panel-1");
assert.equal(parsed[0].order, 1);
assert.deepEqual(parsed[0].xyxy, [500, 0, 1000, 700]);
assert.equal(parsed[0].confidence, 0.95);
assert.equal(parsed[0].polygons?.length, 1);

assert.equal(parsed[1].id, "panel-2");
assert.equal(parsed[1].order, 2);

assert.equal(parsed[2].id, "panel-3");
assert.equal(parsed[2].order, 3);
assert.equal(parsed[2].polygons, undefined);

// Test 3: Array format without root object
const arrayPayload = [
  { id: "p1", order: 1, xyxy: [0, 0, 100, 100] },
  { id: "p2", order: 2, xyxy: [100, 0, 200, 100] },
];
const parsedArray = parsePanelDetections(arrayPayload);
assert.equal(parsedArray.length, 2);
assert.equal(parsedArray[0].id, "p1");
assert.equal(parsedArray[1].id, "p2");

// Test 4: normalizePanelBoxes
const normalized = normalizePanelBoxes(parsed);
assert.equal(normalized.length, 3);
assert.deepEqual(normalized[0], [500, 0, 1000, 700]);

console.log("All panelRegions unit tests passed successfully!");
