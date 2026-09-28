import assert from "node:assert/strict";
import { approveMangaReview } from "./approveMangaReview";

const requests: Array<{ url: string; method?: string; headers?: Record<string, string> }> = [];
const mockFetcher: typeof fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  requests.push({
    url: String(input),
    method: init?.method,
    headers: init?.headers as Record<string, string>,
  });
  return {
    ok: true,
    json: async () => ({
      groupId: "test-manga-id",
      mangaTitle: "Sample Manga",
      approvedCount: 3,
      status: "approved",
    }),
  } as Response;
}) as typeof fetch;

const result = await approveMangaReview("test-manga-id", "Sample Manga", mockFetcher);
assert.equal(result.approvedCount, 3);
assert.equal(result.status, "approved");
assert.equal(requests.length, 1);
assert.equal(requests[0].method, "POST");
assert.equal(
  new URL(requests[0].url, "http://localhost").pathname,
  "/manga/test-manga-id/review/approve-all"
);

// Test error handling
const failingFetcher: typeof fetch = (async () => ({
  ok: false,
  status: 404,
  json: async () => ({ detail: "Manga group not found" }),
})) as unknown as typeof fetch;

await assert.rejects(
  async () => approveMangaReview("unknown-id", "Unknown", failingFetcher),
  { message: "Manga group not found" }
);

console.log("approveMangaReview unit tests passed successfully!");
