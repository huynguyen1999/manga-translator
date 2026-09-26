import assert from "node:assert/strict";
import type { Dispatch, SetStateAction } from "react";
import type { FinishedImage } from "@/types";
import { createGalleryMoveActions } from "./movePages";

const first: FinishedImage = {
  id: "page-1", originalName: "001.png", result: "/1.png", folder: "folder-1",
  mangaTitle: "First", finishedAt: new Date("2026-01-01"), settings: {},
};
const second: FinishedImage = {
  id: "page-2", originalName: "002.png", result: "/2.png", folder: "folder-2",
  mangaTitle: "First", finishedAt: new Date("2026-01-02"), settings: {},
};
const destination: FinishedImage = {
  id: "page-3", originalName: "003.png", result: "/3.png", folder: "folder-3",
  mangaTitle: "Target", finishedAt: new Date("2026-01-03"), settings: {},
};
const createState = <T>(initial: T) => {
  let value = initial;
  const set: Dispatch<SetStateAction<T>> = (next) => {
    value = typeof next === "function" ? (next as (previous: T) => T)(value) : next;
  };
  return { get value() { return value; }, set };
};

const mangaImages = createState<Record<string, FinishedImage[]>>({ First: [first, second], Target: [destination] });
const singleImage = createState<FinishedImage | null>(null);
const modalOpen = createState(true);
const targetTitle = createState(" Target ");
const selected = new Set(["page-1"]);
const updates: unknown[][] = [];
let clearCount = 0;
const { handleMoveSelected } = createGalleryMoveActions({
  singleImageToMove: null,
  selectedImageIds: selected,
  allLoadedImages: [first, second, destination],
  onUpdateMangaTitle: (...args) => { updates.push(args); },
  clearSelectedImages: () => { clearCount += 1; },
  setMangaImages: mangaImages.set,
  setSingleImageToMove: singleImage.set,
  setIsMoveModalOpen: modalOpen.set,
  setTargetMangaName: targetTitle.set,
});

handleMoveSelected(targetTitle.value);
assert.deepEqual(updates, [[["page-1"], "Target", undefined, undefined, ["folder-1"]]]);
assert.deepEqual(mangaImages.value.First.map((image) => image.id), ["page-2"]);
assert.deepEqual(mangaImages.value.Target.map((image) => image.id), ["page-1", "page-3"]);
assert.equal(mangaImages.value.Target[0].mangaTitle, "Target");
assert.equal(clearCount, 1);
assert.equal(singleImage.value, null);
assert.equal(modalOpen.value, false);
assert.equal(targetTitle.value, "");

console.log("gallery move-page action contracts passed");
