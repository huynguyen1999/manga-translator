import assert from "node:assert/strict";
import { shouldFocusMangaSearch } from "./GalleryFilterControls";
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

function searchKeyEvent({
  key = "/",
  targetTagName = "DIV",
  isContentEditable = false,
  altKey = false,
  ctrlKey = false,
  metaKey = false,
}: {
  key?: string;
  targetTagName?: string;
  isContentEditable?: boolean;
  altKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
} = {}) {
  return {
    key,
    altKey,
    ctrlKey,
    metaKey,
    target: { tagName: targetTagName, isContentEditable },
  } as unknown as KeyboardEvent;
}

assert.equal(shouldFocusMangaSearch(searchKeyEvent()), true);
assert.equal(shouldFocusMangaSearch(searchKeyEvent({ targetTagName: "INPUT" })), false);
assert.equal(shouldFocusMangaSearch(searchKeyEvent({ targetTagName: "TEXTAREA" })), false);
assert.equal(shouldFocusMangaSearch(searchKeyEvent({ targetTagName: "SELECT" })), false);
assert.equal(shouldFocusMangaSearch(searchKeyEvent({ isContentEditable: true })), false);
assert.equal(shouldFocusMangaSearch(searchKeyEvent({ key: "?" })), false);
assert.equal(shouldFocusMangaSearch(searchKeyEvent({ ctrlKey: true })), false);
assert.equal(shouldFocusMangaSearch(searchKeyEvent({ metaKey: true })), false);
assert.equal(shouldFocusMangaSearch(searchKeyEvent({ altKey: true })), false);

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
