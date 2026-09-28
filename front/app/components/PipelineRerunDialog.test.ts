import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { PipelineRerunDialog } from "./PipelineRerunDialog";
import { PRESETS } from "./pipelineRerunPresets";
import type { FinishedImage } from "@/types";

// 1. Presets tests
assert.equal(PRESETS.length, 4);
const presetIds = PRESETS.map((p) => p.id);
assert.deepEqual(presetIds, [
  "reprocess_text",
  "typesetting",
  "translation_typesetting",
  "full",
]);

for (const preset of PRESETS) {
  assert.ok(preset.title, `Preset ${preset.id} must have title`);
  assert.ok(preset.description, `Preset ${preset.id} must have description`);
  assert.ok(preset.summarySteps, `Preset ${preset.id} must have summarySteps`);
  assert.ok(preset.details.length > 0, `Preset ${preset.id} must have details`);
}

// 2. Component static markup rendering test
const sampleImage: FinishedImage = {
  id: "page-rerun-1",
  originalName: "test_page_01.png",
  folder: "folder-rerun-1",
  result: "/result/folder-rerun-1/final.jpg",
  finishedAt: new Date(),
  settings: {
    translator: "deepseek",
    renderFont: "comic",
  },
};

const markup = renderToStaticMarkup(
  React.createElement(PipelineRerunDialog, {
    images: [sampleImage],
    mangaTitle: "Test Series",
    appSettings: {
      translator: "gemini",
      targetLanguage: "ENG",
      renderFont: "wildwords",
    },
    onClose: () => {},
    onSubmit: () => {},
  })
);

assert.ok(markup.includes("Rerun Pipeline"), "Should contain dialog title");
assert.ok(markup.includes("Pipeline Settings Source"), "Should contain settings source header");
assert.ok(markup.includes("Current App Settings"), "Should contain app settings option");
assert.ok(markup.includes("Page Snapshot Settings"), "Should contain snapshot settings option");
assert.ok(markup.includes("test_page_01.png"), "Should contain page original name");
assert.ok(markup.includes("Test Series"), "Should contain manga title");

console.log("PipelineRerunDialog tests passed successfully!");
