import assert from "node:assert/strict";
import type { Dispatch, SetStateAction } from "react";
import type { FinishedImage } from "@/types";
import { createGalleryRenameActions } from "./renameActions";

const image: FinishedImage = {
  id: "page-1",
  originalName: "001.png",
  result: "/page.png",
  folder: "page-folder",
  mangaTitle: "Old title",
  finishedAt: new Date("2026-01-01"),
  settings: {},
};
const group = { id: "group-1", title: "Old title", images: [image] };
const createState = <T>(initial: T) => {
  let value = initial;
  const set: Dispatch<SetStateAction<T>> = (next) => {
    value = typeof next === "function" ? (next as (previous: T) => T)(value) : next;
  };
  return { get value() { return value; }, set };
};

const storage = new Map<string, string>([
  ["manga-read-pos-Old title", "12"],
  ["manga-read-page-Old title", "page-1"],
  ["manga-read-count-Old title", "40"],
  ["manga-read-page-New title", "existing-page"],
]);
const originalWindowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
Object.defineProperty(globalThis, "window", {
  configurable: true,
  value: {
    localStorage: {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
    },
  },
});

const mangaImages = createState<Record<string, FinishedImage[]>>({ "Old title": [image] });
const expandedGroups = createState<Record<string, boolean>>({ "Old title": true });
const selectedMangaIds = createState(new Map([["group-1", "Old title"]]));
const activeFilter = createState("Old title");
const renaming = createState<string | null>(null);
const renameInput = createState(" New title ");
const updates: unknown[][] = [];
const openedDetails: string[] = [];
const actions = createGalleryRenameActions({
  mangaGroups: [group],
  renameInputValue: renameInput.value,
  activeMangaFilter: activeFilter.value,
  onUpdateMangaTitle: (...args) => { updates.push(args); },
  onOpenMangaDetail: (id) => openedDetails.push(id),
  setActiveMangaFilter: activeFilter.set,
  setMangaImages: mangaImages.set,
  setExpandedGroups: expandedGroups.set,
  setSelectedMangaIds: selectedMangaIds.set,
  setRenamingManga: renaming.set,
  setRenameInputValue: renameInput.set,
});

try {
  actions.handleStartRename("Old title");
  assert.equal(renaming.value, "Old title");
  assert.equal(renameInput.value, "Old title");

  const saveActions = createGalleryRenameActions({
    mangaGroups: [group],
    renameInputValue: " New title ",
    activeMangaFilter: activeFilter.value,
    onUpdateMangaTitle: (...args) => { updates.push(args); },
    onOpenMangaDetail: (id) => openedDetails.push(id),
    setActiveMangaFilter: activeFilter.set,
    setMangaImages: mangaImages.set,
    setExpandedGroups: expandedGroups.set,
    setSelectedMangaIds: selectedMangaIds.set,
    setRenamingManga: renaming.set,
    setRenameInputValue: renameInput.set,
  });
  saveActions.handleSaveRename("Old title");

  assert.deepEqual(updates, [[ ["page-1"], "New title", "Old title", "group-1", ["page-folder"] ]]);
  assert.deepEqual(openedDetails, ["group-1"]);
  assert.equal(storage.get("manga-read-pos-New title"), "12");
  assert.equal(storage.get("manga-read-page-New title"), "existing-page");
  assert.equal(storage.has("manga-read-page-Old title"), false);
  assert.equal(storage.get("manga-read-count-New title"), "40");
  assert.equal(mangaImages.value["New title"][0].mangaTitle, "New title");
  assert.equal(mangaImages.value["Old title"], undefined);
  assert.equal(expandedGroups.value["New title"], true);
  assert.equal(selectedMangaIds.value.get("group-1"), "New title");
  assert.equal(renaming.value, null);
} finally {
  if (originalWindowDescriptor) {
    Object.defineProperty(globalThis, "window", originalWindowDescriptor);
  } else {
    Reflect.deleteProperty(globalThis, "window");
  }
}

console.log("gallery rename action contracts passed");
