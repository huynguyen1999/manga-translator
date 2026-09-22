import assert from "node:assert/strict";
import { getReaderPriorityIndices, getReaderPreloadIndices, getSeriesMemberNavigation } from "../components/MangaReaderModal";
import { parseAppPath, buildReaderUrl, mangaIdForTitle, validatePriorRoute } from "./routeState";

console.log("Running mangaReaderJump tests...");

// 1. Verify 385-page reader manifest simulation
const mockFullManifestItem = {
  id: "page_143",
  folder: "folder_143",
  originalName: "143.png",
  resultUrl: "/result/folder_143/final.png",
  inputUrl: "/result/folder_143/input.png",
  inpaintedUrl: "/result/folder_143/inpainted.jpg",
  textRegionsUrl: "/result/folder_143/text_regions.json",
  hasTextRegions: true,
  mangaTitle: "[Higashiyama Show] The Girllove Diary",
  finishedAt: new Date().toISOString(),
  settings: {
    translator: "gemini",
    targetLanguage: "en",
    textDetector: "default",
    detectionResolution: "1536",
    inpainter: "default",
    inpaintingSize: "2048",
    renderTextDirection: "auto",
    customUnclipRatio: 2.3,
    customBoxThreshold: 0.7,
    maskDilationOffset: 2,
  },
};

// Slim reader item strips heavy fields: settings, textRegionsUrl, inpaintedUrl, hasTextRegions
const mockSlimManifestItem = {
  id: "page_143",
  folder: "folder_143",
  originalName: "143.png",
  resultUrl: "/result/folder_143/final.png",
  inputUrl: "/result/folder_143/input.png",
  mangaTitle: "[Higashiyama Show] The Girllove Diary",
  finishedAt: mockFullManifestItem.finishedAt,
};

const fullJson = JSON.stringify(Array(385).fill(mockFullManifestItem));
const slimJson = JSON.stringify(Array(385).fill(mockSlimManifestItem));

console.log(`Full 385-page manifest size: ${(fullJson.length / 1024).toFixed(1)} KB`);
console.log(`Slim 385-page manifest size: ${(slimJson.length / 1024).toFixed(1)} KB`);
assert.ok(
  slimJson.length < fullJson.length * 0.4,
  "Slim reader manifest must be substantially smaller than full manifest (<40% of full size)"
);

// 2. Prepare pages in both directions, including reverse scrolls past two pages.
// Fast jump from page 29 to page 143
const prefetchWindowPage143 = getReaderPreloadIndices(143, 385, false);
assert.equal(prefetchWindowPage143[0], 142, "Visible page 143 (index 142) must be prioritized first");
for (let i = 1; i <= 5; i++) {
  assert.equal(prefetchWindowPage143[i], 142 + i, `Ahead page offset +${i} must match`);
}
assert.deepEqual(prefetchWindowPage143.slice(6), [141, 140, 139, 138, 137], "Prepare five pages behind for fast reverse scrolling");

// Edge case: page 1
const prefetchWindowPage1 = getReaderPreloadIndices(1, 385, false);
assert.equal(prefetchWindowPage1[0], 0, "Page 1 index 0 must be first");
assert.equal(prefetchWindowPage1.length, 6, "At start of manga, window has the visible page plus five ahead");

// Edge case: page 385 (last page)
const prefetchWindowPage385 = getReaderPreloadIndices(385, 385, false);
assert.equal(prefetchWindowPage385[0], 384, "Page 385 index 384 must be first");
assert.deepEqual(prefetchWindowPage385, [384, 383, 382, 381, 380, 379]);

const phonePrefetchWindow = getReaderPreloadIndices(143, 385, true);
assert.deepEqual(phonePrefetchWindow, prefetchWindowPage143, "Phones must preload the same bidirectional window");

assert.deepEqual(
  getReaderPriorityIndices(143, 385),
  [137, 138, 139, 140, 141, 142, 143, 144, 145, 146, 147],
  "Reader should prepare five pages in both directions",
);
assert.deepEqual(getReaderPriorityIndices(1, 3), [0, 1, 2], "Priority window must clamp at chapter start");

// 3. Verify route parsing and navigation for [Higashiyama Show] The Girllove Diary
const readerUrl = buildReaderUrl("[Higashiyama Show] The Girllove Diary");
assert.equal(
  readerUrl,
  `/read?manga=${mangaIdForTitle("[Higashiyama Show] The Girllove Diary")}`,
  "Reader URL must use the manga ID"
);

const parsed = parseAppPath("/read", "?manga=%5BHigashiyama%20Show%5D%20The%20Girllove%20Diary");
assert.equal(parsed.view, "gallery");
assert.equal(parsed.overlay, "reader");
assert.equal(parsed.mangaTitle, "[Higashiyama Show] The Girllove Diary");

// Modal close destination
assert.equal(validatePriorRoute("/gallery"), "/gallery");
assert.equal(validatePriorRoute("/studio"), "/studio");
assert.equal(validatePriorRoute(undefined), "/gallery");

const seriesMembers = [
  { id: "one", title: "Manga 1", position: 1, count: 3 },
  { id: "two", title: "Manga 2", position: 2, count: 4 },
  { id: "three", title: "Manga 3", position: 3, count: 5 },
];
assert.equal(getSeriesMemberNavigation(seriesMembers, "one").previous, null);
assert.equal(getSeriesMemberNavigation(seriesMembers, "one").next?.id, "two");
assert.equal(getSeriesMemberNavigation(seriesMembers, "three").next, null);
assert.equal(getSeriesMemberNavigation(seriesMembers, "two").previous?.id, "one");

console.log("All mangaReaderJump tests passed successfully!");
