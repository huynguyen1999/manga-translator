import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import type { FinishedImage } from "@/types";
import { MangaRowGroup } from "./MangaRowGroup";

type Props = React.ComponentProps<typeof MangaRowGroup>;

const image: FinishedImage = {
  id: "page-1",
  originalName: "001.png",
  result: "/result/page-1/final.png",
  folder: "page-1",
  mangaTitle: "Chapter 1",
  finishedAt: new Date("2026-01-01"),
  settings: {},
};

const group: Props["group"] = {
  id: "chapter-1",
  title: "Chapter 1",
  count: 1,
  coverImage: image,
  images: [image],
  seriesId: null,
  seriesTitle: null,
  isLoading: false,
};

const state: Props["state"] = {
  isCollapsed: false,
  isDownloading: false,
  isRenaming: false,
  allInGroupSelected: false,
  readProgress: { page: null, complete: false },
  isRowHighlighted: false,
  renameInputValue: "",
  selectedImageIds: new Set(),
  highlightedImageId: null,
  readerLoadingTitle: null,
  readerLoadError: null,
  summarizingTitle: null,
  reviewOnly: false,
  pageViewState: { from: "/results" },
};

const actions: Props["actions"] = {
  toggleGroupCollapse: () => {},
  setRenameInputValue: () => {},
  handleSaveRename: () => {},
  setRenamingManga: () => {},
  handleStartRename: () => {},
  setAssigningManga: () => {},
  handleToggleSelectAll: async () => {},
  handleReadManga: async () => {},
  handleSummarize: async () => {},
  handleDownloadCbz: async () => {},
  onDeleteManga: async () => {},
  setConfirmDeleteManga: () => {},
  onMoveImage: () => {},
  onReadFromHere: () => {},
  onClickImage: () => {},
  onDownloadImage: () => {},
  onDeleteImage: () => {},
  handleCardDelete: () => {},
  onEditImage: () => {},
  onRerenderImage: async () => {},
  toggleSelectImage: () => {},
};

const renderGroup = (nextState: Props["state"]) =>
  renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      null,
      React.createElement(MangaRowGroup, { group, state: nextState, actions }),
    ),
  );

const expanded = renderGroup(state);
assert.match(expanded, /data-manga-id="chapter-1"/);
assert.match(expanded, /Translated CBZ/);
assert.match(expanded, /Original CBZ/);
assert.match(expanded, /Select All/);
assert.match(expanded, /001\.png/);

const collapsed = renderGroup({ ...state, isCollapsed: true });
assert.doesNotMatch(collapsed, /001\.png/);

const renaming = renderGroup({
  ...state,
  isRenaming: true,
  renameInputValue: "Updated chapter",
});
assert.match(renaming, /value="Updated chapter"/);
assert.match(renaming, /title="Cancel"/);

const loading = renderGroup({
  ...state,
  isDownloading: true,
  readerLoadError: group.title,
});
assert.match(loading, /Packaging CBZ/);
assert.match(loading, /Retry Read/);
