import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  GalleryEmptyState,
  GalleryLoadingState,
  GalleryNoResultsState,
  GalleryReviewClearState,
} from "./GalleryStatusStates";

const cardsLoading = renderToStaticMarkup(React.createElement(GalleryLoadingState, { viewMode: "cards" }));
assert.match(cardsLoading, /aria-label="Loading manga gallery"/);
assert.match(cardsLoading, /Loading library/);
assert.equal((cardsLoading.match(/aspect-\[3\/4\]/g) || []).length, 12);

const rowsLoading = renderToStaticMarkup(React.createElement(GalleryLoadingState, { viewMode: "rows" }));
assert.match(rowsLoading, /space-y-4/);
assert.equal((rowsLoading.match(/h-16 rounded-xl/g) || []).length, 5);

const reviewClear = renderToStaticMarkup(React.createElement(GalleryReviewClearState, {
  onBackToGallery: () => {},
}));
assert.match(reviewClear, /Review queue is clear/);
assert.match(reviewClear, /Back to gallery/);

const empty = renderToStaticMarkup(React.createElement(GalleryEmptyState));
assert.match(empty, /No manga yet/);
assert.match(empty, /Upload manga pages from Studio to create a manga\./);

const noResults = renderToStaticMarkup(React.createElement(GalleryNoResultsState, {
  searchQuery: "chapter 9",
  statusFilter: "translated",
  onResetFilters: () => {},
}));
assert.match(noResults, /No translated manga matching “chapter 9”/);
assert.match(noResults, /Reset filters/);
