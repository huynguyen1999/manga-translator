import assert from "node:assert/strict";
import type { Dispatch, SetStateAction } from "react";
import type { FinishedImage } from "@/types";
import type { PageSortOption } from "@/utils/resultGallery";
import { createPageOrderActions } from "./pageOrderActions";

const images: FinishedImage[] = [
  { id: "b", originalName: "002.png", result: "/b.png", finishedAt: new Date("2026-01-02"), settings: {} },
  { id: "a", originalName: "001.png", result: "/a.png", finishedAt: new Date("2026-01-01"), settings: {} },
];
const group = { id: "group", title: "Manga", images };

const createState = <T>(initial: T) => {
  let value = initial;
  const set: Dispatch<SetStateAction<T>> = (next) => {
    value = typeof next === "function" ? (next as (previous: T) => T)(value) : next;
  };
  return { get value() { return value; }, set };
};

const savedOrders: string[][] = [];
const pageImages = createState<Record<string, FinishedImage[]>>({});
const sort = createState<PageSortOption>("name-asc");
const error = createState<string | null>(null);
const reordering = createState<string | null>(null);
const actions = createPageOrderActions({
  currentSingleGroup: group,
  onReorderMangaPages: async (_groupId, pageIds) => { savedOrders.push(pageIds); },
  pageSort: sort.value,
  setPageSort: sort.set,
  setMangaImages: pageImages.set,
  setPageOrderError: error.set,
  setReorderingGroupId: reordering.set,
});

await actions.handlePageDrop(group, "a", "b");
assert.deepEqual(savedOrders[0], ["a", "b"]);
assert.deepEqual(pageImages.value.Manga.map((image) => image.pageOrder), [1, 2]);
assert.equal(reordering.value, null);

const failed = createPageOrderActions({
  currentSingleGroup: group,
  onReorderMangaPages: async () => { throw new Error("save failed"); },
  pageSort: "name-asc",
  setPageSort: sort.set,
  setMangaImages: pageImages.set,
  setPageOrderError: error.set,
  setReorderingGroupId: reordering.set,
});
await failed.handlePageDrop(group, "a", "b");
assert.deepEqual(pageImages.value.Manga, images);
assert.equal(error.value, "save failed");
assert.equal(reordering.value, null);

await actions.handleSavePageSort();
assert.equal(sort.value, "order");
assert.deepEqual(savedOrders[1], ["a", "b"]);

const unchanged = createPageOrderActions({
  currentSingleGroup: { ...group, images: [...images].reverse() },
  onReorderMangaPages: async () => { throw new Error("must not save"); },
  pageSort: "name-asc",
  setPageSort: sort.set,
  setMangaImages: pageImages.set,
  setPageOrderError: error.set,
  setReorderingGroupId: reordering.set,
});
await unchanged.handleSavePageSort();
assert.equal(sort.value, "order");

console.log("gallery page order action contracts passed");
