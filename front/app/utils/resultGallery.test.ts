import assert from "node:assert/strict";
import type { FinishedImage } from "@/types";
import { getLastReadPageIndex, getMangaReadProgress, mergeGalleryImages } from "./resultGallery";

assert.deepEqual(getMangaReadProgress("12", "12", 12), { page: 12, complete: true });
assert.deepEqual(getMangaReadProgress("11", "12", 12), { page: 11, complete: false });
assert.deepEqual(getMangaReadProgress("12", "11", 12), { page: null, complete: false });
assert.deepEqual(getMangaReadProgress(null, null, 12), { page: null, complete: false });

assert.equal(getLastReadPageIndex("12", "12", 12), 11);
assert.equal(getLastReadPageIndex("1", "12", 12), 0);
assert.equal(getLastReadPageIndex("11", "12", 12), 10);
assert.equal(getLastReadPageIndex("12", "11", 12), null);
assert.equal(getLastReadPageIndex(null, null, 12), null);
assert.equal(getLastReadPageIndex("0", "12", 12), null);
const image = (id: string, folder: string, originalName: string): FinishedImage => ({
  id,
  folder,
  originalName,
  result: `/result/${folder}/final.png`,
  finishedAt: new Date(0),
  settings: {},
});

const loaded = [image("server-4", "folder-4", "4.jpg")];
const session = [image("session-4", "folder-4", "4.jpg"), image("session-5", "folder-5", "5.jpg")];

assert.deepEqual(
  mergeGalleryImages(loaded, session).map((item) => item.folder),
  ["folder-4", "folder-5"]
);

console.log("result gallery merge checks passed");
