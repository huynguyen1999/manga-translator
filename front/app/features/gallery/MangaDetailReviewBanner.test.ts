import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MangaDetailReviewBanner } from "./MangaDetailReviewBanner";

// Test 1: Returns null when needsReviewCount is 0
const nullOutput = renderToStaticMarkup(
  React.createElement(MangaDetailReviewBanner, {
    needsReviewCount: 0,
    hasImages: true,
    mangaTitle: "Sample Manga",
    onReviewNextPage: () => {},
    onAcceptAll: () => {},
    isAccepting: false,
  })
);
assert.equal(nullOutput, "");

// Test 2: Renders review banner with Accept all button and message when needsReviewCount > 0
const bannerOutput = renderToStaticMarkup(
  React.createElement(MangaDetailReviewBanner, {
    needsReviewCount: 5,
    hasImages: true,
    mangaTitle: "Sample Manga",
    onReviewNextPage: () => {},
    onAcceptAll: () => {},
    isAccepting: false,
  })
);
assert.match(bannerOutput, /5 flagged pages remain/);
assert.match(bannerOutput, /Review next page/);
assert.match(bannerOutput, /Accept all/);

// Test 3: Shows loading text when isAccepting is true
const loadingOutput = renderToStaticMarkup(
  React.createElement(MangaDetailReviewBanner, {
    needsReviewCount: 1,
    hasImages: true,
    mangaTitle: "Sample Manga",
    onReviewNextPage: () => {},
    onAcceptAll: () => {},
    isAccepting: true,
  })
);
assert.match(loadingOutput, /1 flagged page remain/);
assert.match(loadingOutput, /Accepting…/);

console.log("MangaDetailReviewBanner unit tests passed successfully!");
