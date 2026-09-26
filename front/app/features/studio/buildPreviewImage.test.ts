import assert from "node:assert/strict";
import { apiUrl } from "@/utils/api";
import { buildStudioPreviewImage } from "./buildPreviewImage";

const file = new File(["page"], "page.png", { type: "image/png" });
const preview = buildStudioPreviewImage({
  file,
  result: "https://studio.test/result/folder-1/final.jpg",
  sourceType: "translated",
  folderMap: new Map(),
  settings: { targetLanguage: "ENG" },
  options: {
    mangaTitle: "Test Manga",
    settings: { targetLanguage: "FRA" },
    offlineModel: "model-a",
  },
});

assert.ok(preview);
assert.equal(preview.originalName, "page.png");
assert.equal(preview.folder, "folder-1");
assert.equal(preview.inputUrl, apiUrl("/result/folder-1/input.png"));
assert.equal(preview.inpaintedUrl, "/result/folder-1/inpainted.jpg");
assert.equal(preview.textRegionsUrl, "/result/folder-1/text_regions.json");
assert.equal(preview.hasTextRegions, true);
assert.equal(preview.mangaTitle, "Test Manga");
assert.equal(preview.settings.targetLanguage, "FRA");
assert.equal(preview.settings.offlineModel, "model-a");

assert.equal(buildStudioPreviewImage({
  file: "source/page.png",
  result: null,
  folderMap: new Map(),
  settings: {},
}), null);

console.log("studio preview image builder contracts passed");
