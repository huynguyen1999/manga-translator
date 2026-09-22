import assert from "node:assert/strict";
import { MangaImageCache } from "./mangaImageCache";

// Setup mocks/spies for global environment in node / vite-node
const revokedUrls: string[] = [];
let objectUrlCounter = 0;

if (typeof globalThis.URL.createObjectURL !== "function") {
  globalThis.URL.createObjectURL = (_blob: any) => `blob:mock-url-${++objectUrlCounter}`;
}

const originalRevoke = globalThis.URL.revokeObjectURL;
globalThis.URL.revokeObjectURL = (url: string) => {
  revokedUrls.push(url);
  if (typeof originalRevoke === "function") {
    try {
      originalRevoke(url);
    } catch {
      // ignore
    }
  }
};

async function runTests() {
  console.log("Running mangaImageCache tests...");

  // Mock fetch
  let fetchCounts: Record<string, number> = {};
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: any, init?: any) => {
    const url = typeof input === "string" ? input : input.url;
    fetchCounts[url] = (fetchCounts[url] || 0) + 1;

    if (url.includes("fail")) {
      return {
        ok: false,
        status: 404,
        statusText: "Not Found",
      } as any;
    }

    // Small delay to simulate async network latency
    await new Promise((r) => setTimeout(r, 10));

    if (init?.signal?.aborted) {
      const err = new Error("Aborted");
      err.name = "AbortError";
      throw err;
    }

    return {
      ok: true,
      status: 200,
      statusText: "OK",
      clone: () => ({ ok: true }),
      blob: async () => new Blob(["fake-image-bytes"], { type: "image/png" }),
    } as any;
  }) as any;

  try {
    // Test 1: Empty cache check
    const cache = new MangaImageCache({ maxEntries: 20, concurrency: 2 });
    assert.equal(cache.isLoaded("http://example.com/page1.png"), false);
    assert.equal(cache.get("http://example.com/page1.png"), undefined);

    // Test 2: fetchAndCache basic flow
    const entry1 = await cache.fetchAndCache("http://example.com/page1.png");
    assert.ok(entry1);
    assert.equal(entry1?.status, "loaded");
    assert.equal(entry1?.url, "http://example.com/page1.png");
    assert.ok(entry1?.blobUrl?.startsWith("blob:"));
    assert.equal(cache.isLoaded("http://example.com/page1.png"), true);
    assert.equal(cache.get("http://example.com/page1.png")?.status, "loaded");
    assert.equal(fetchCounts["http://example.com/page1.png"], 1);

    // Test 3: Cache hit - subsequent fetchAndCache does not re-fetch
    const entry1Again = await cache.fetchAndCache("http://example.com/page1.png");
    assert.equal(entry1Again?.blobUrl, entry1?.blobUrl);
    assert.equal(fetchCounts["http://example.com/page1.png"], 1, "Should not re-fetch cached image");

    // Test 4: In-flight deduplication
    const pA = cache.fetchAndCache("http://example.com/page2.png");
    const pB = cache.fetchAndCache("http://example.com/page2.png");
    const [resA, resB] = await Promise.all([pA, pB]);
    assert.equal(resA?.blobUrl, resB?.blobUrl);
    assert.equal(fetchCounts["http://example.com/page2.png"], 1, "Deduplication should ensure single network call");

    // Test 5: Subscription notifications
    let notificationCount = 0;
    let lastNotificationStatus = "";
    const unsub = cache.subscribe("http://example.com/page3.png", (e) => {
      notificationCount++;
      lastNotificationStatus = e.status;
    });

    await cache.fetchAndCache("http://example.com/page3.png");
    assert.ok(notificationCount >= 1);
    assert.equal(lastNotificationStatus, "loaded");
    unsub();

    // Test 6: Prefetching next 5 images
    const prefetchUrls = [
      "http://example.com/next-page4.png",
      "http://example.com/next-page5.png",
      "http://example.com/next-page6.png",
      "http://example.com/next-page7.png",
      "http://example.com/next-page8.png",
    ];

    cache.prefetchImages(prefetchUrls);
    // Wait for prefetch queue to drain (5 requests with concurrency 2, ~30-50ms)
    await new Promise((r) => setTimeout(r, 150));

    for (const u of prefetchUrls) {
      assert.equal(cache.isLoaded(u), true, `URL ${u} should be loaded after prefetching`);
    }

    // Test 7: LRU Eviction & Object URL cleanup
    const smallCache = new MangaImageCache({ maxEntries: 2, concurrency: 1 });
    const initialRevokeCount = revokedUrls.length;
    await smallCache.fetchAndCache("http://example.com/lru-1.png");
    await smallCache.fetchAndCache("http://example.com/lru-2.png");
    assert.equal(smallCache.isLoaded("http://example.com/lru-1.png"), true);
    assert.equal(smallCache.isLoaded("http://example.com/lru-2.png"), true);

    // Adding 3rd item should evict lru-1.png and call revokeObjectURL
    await smallCache.fetchAndCache("http://example.com/lru-3.png");
    assert.equal(smallCache.isLoaded("http://example.com/lru-1.png"), false, "lru-1 should be evicted");
    assert.equal(smallCache.isLoaded("http://example.com/lru-2.png"), true, "lru-2 should still be cached");
    assert.equal(smallCache.isLoaded("http://example.com/lru-3.png"), true, "lru-3 should be cached");
    assert.ok(revokedUrls.length > initialRevokeCount, "Evicted item should have Object URL revoked");

    // Test 8: Error handling
    const failEntry = await cache.fetchAndCache("http://example.com/fail.png");
    assert.equal(failEntry?.status, "error");
    assert.equal(cache.isLoaded("http://example.com/fail.png"), false);

    // Test 9: Cancel pending
    cache.cancelPending();

    // Test 10: Clear
    cache.clear();
    assert.equal(cache.isLoaded("http://example.com/next-page4.png"), false);

    console.log("All mangaImageCache tests passed successfully!");
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.URL.revokeObjectURL = originalRevoke;
  }
}

runTests().catch((err) => {
  console.error("Test failed:", err);
  process.exit(1);
});
