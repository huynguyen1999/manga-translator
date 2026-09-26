import assert from "node:assert/strict";
import { buildGalleryPageUrl, parseAppPath } from "../utils/routeState";
import { filterMangaGroupsByStatus, sortMangaGroups } from "../utils/resultGallery";

const url = buildGalleryPageUrl(2, 50, "One Piece", "manga", "alpha-asc", false, "translated");
assert.equal(url, "/gallery?page=2&pageSize=50&search=One+Piece&sort=alpha-asc&status=translated");

const parsedUrl = new URL(url, "http://studio.test");
const route = parseAppPath(parsedUrl.pathname, parsedUrl.search);
assert.equal(route.view, "gallery");
assert.equal(route.galleryPage, 2);
assert.equal(route.galleryPageSize, 50);
assert.equal(route.gallerySearch, "One Piece");
assert.equal(route.galleryStatus, "translated");

const image = (id: string, sourceType: "original" | "translated") => ({
  id,
  originalName: `${id}.png`,
  result: `/result/${id}.png`,
  sourceType,
  finishedAt: new Date(0),
  settings: {},
});
const groups = [
  { title: "Zeta", cover: image("zeta", "translated"), hasSummary: true },
  { title: "One Piece 10", cover: image("one-piece-10", "translated"), hasSummary: false },
  { title: "One Piece 2", cover: image("one-piece-2", "translated"), hasSummary: false },
  { title: "Ungrouped", cover: image("original", "original"), hasSummary: false },
];
const visible = sortMangaGroups(
  filterMangaGroupsByStatus(groups, route.galleryStatus ?? "all")
    .filter(({ title }) => title.startsWith(route.gallerySearch ?? "")),
  "alpha-asc",
);
assert.deepEqual(visible.map(({ title }) => title), ["One Piece 2", "One Piece 10"]);

console.log("gallery flow contracts passed");
