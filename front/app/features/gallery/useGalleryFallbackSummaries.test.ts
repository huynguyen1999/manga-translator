import assert from "node:assert/strict";
import type { MangaGroupSummary } from "@/types";
import { fetchFallbackGallerySummaries } from "./useGalleryFallbackSummaries";

const groups = [{ id: "manga-1", title: "Manga", count: 2 }] as MangaGroupSummary[];
let requestedUrl = "";
const loaded = await fetchFallbackGallerySummaries(async (input) => {
  requestedUrl = String(input);
  return { json: async () => ({ groups }) } as Response;
});
assert.deepEqual(loaded, groups);
assert.ok(requestedUrl.endsWith("/results/groups"));

const malformed = await fetchFallbackGallerySummaries(async () => (
  { json: async () => ({ groups: null }) } as Response
));
assert.equal(malformed, undefined, "invalid payload leaves existing fallback summaries untouched");
