import assert from "node:assert/strict";
import type { Dispatch, SetStateAction } from "react";
import type { FinishedImage } from "@/types";
import { applyGalleryEditorSave } from "./editorSave";

const createState = <T,>(initial: T) => {
  let value = initial;
  const set: Dispatch<SetStateAction<T>> = (update) => {
    value = typeof update === "function" ? (update as (current: T) => T)(value) : update;
  };
  return { get value() { return value; }, set };
};

const edited: FinishedImage = {
  id: "page-1",
  originalName: "page.png",
  folder: "folder-1",
  mangaTitle: " New title ",
  reviewStatus: "pending",
  result: "/new.png",
  finishedAt: new Date(0),
  settings: {},
};
const matchingFolder: FinishedImage = {
  ...edited,
  id: "other-id",
  mangaTitle: "New title",
};
const pendingPage: FinishedImage = {
  ...edited,
  id: "page-2",
  folder: "folder-2",
  mangaTitle: "New title",
};
const gallery = createState<Record<string, FinishedImage[]>>({
  "New title": [matchingFolder, pendingPage],
});
const selected = createState<FinishedImage | null>({ ...edited, result: "/old.png" });
const editing = createState<FinishedImage | null>(edited);
const updates: FinishedImage[] = [];
const closed: string[] = [];

applyGalleryEditorSave(edited, {
  editingImage: edited,
  mangaImages: gallery.value,
  selectedImage: selected.value,
  reviewOnly: true,
  setMangaImages: gallery.set,
  setSelectedImage: selected.set,
  setEditingImage: editing.set,
  onUpdateImage: (image) => updates.push(image),
  onCloseMangaDetail: () => closed.push("manga"),
  onCloseOverlay: () => closed.push("overlay"),
});

assert.deepEqual(updates, [edited]);
assert.deepEqual(gallery.value["New title"].map((image) => image.id), ["page-1", "page-2"]);
assert.equal(gallery.value["New title"][0], edited);
assert.equal(selected.value, edited);
assert.equal(editing.value, null);
assert.deepEqual(closed, ["overlay"]);

const noPendingGallery = createState<Record<string, FinishedImage[]>>({
  "New title": [{ ...edited, reviewStatus: "approved" }],
});
const reviewEdit = { ...edited, reviewStatus: "approved" } as FinishedImage;
applyGalleryEditorSave(reviewEdit, {
  editingImage: edited,
  mangaImages: noPendingGallery.value,
  selectedImage: null,
  reviewOnly: true,
  setMangaImages: noPendingGallery.set,
  setSelectedImage: selected.set,
  setEditingImage: editing.set,
  onCloseMangaDetail: () => closed.push("manga"),
  onCloseOverlay: () => closed.push("overlay"),
});
assert.deepEqual(noPendingGallery.value["New title"], []);
assert.equal(closed.at(-1), "manga");

console.log("gallery editor-save contracts passed");
