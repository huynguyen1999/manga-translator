import assert from "node:assert/strict";
import {
  getGalleryEscapeAction,
  shouldClearMangaSelectionOnEscape,
} from "./useGalleryKeyboard";

const clearState = {
  summaryOpen: false,
  createSeriesOpen: false,
  moveMangaOpen: false,
  deleteMangaOpen: false,
  deletePagesOpen: false,
  deleteMangasOpen: false,
};

assert.equal(getGalleryEscapeAction(clearState), null);
assert.equal(getGalleryEscapeAction({ ...clearState, deleteMangaOpen: true }), "delete-manga");
assert.equal(
  getGalleryEscapeAction({ ...clearState, deleteMangaOpen: true, deletePagesOpen: true }),
  "delete-pages",
);
assert.equal(
  getGalleryEscapeAction({ ...clearState, summaryOpen: true, createSeriesOpen: true }),
  "summary",
);
assert.equal(shouldClearMangaSelectionOnEscape(1, clearState), true);
assert.equal(shouldClearMangaSelectionOnEscape(0, clearState), false);
assert.equal(
  shouldClearMangaSelectionOnEscape(1, { ...clearState, moveMangaOpen: true }),
  false,
);
