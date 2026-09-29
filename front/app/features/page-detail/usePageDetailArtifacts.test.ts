import assert from "node:assert/strict";
import {
  loadDiscoveredBubbleMaskUrl,
  loadPipelineManifest,
  loadTranslationArtifacts,
} from "./usePageDetailArtifacts";

const originalFetch = globalThis.fetch;
const requests: Array<{ url: string; init?: RequestInit }> = [];
const responses: Array<{ ok: boolean; body?: unknown }> = [];
globalThis.fetch = (async (input, init) => {
  requests.push({ url: String(input), init });
  const response = responses.shift();
  assert.ok(response);
  return { ok: response.ok, json: async () => response.body } as Response;
}) as typeof fetch;

try {
  responses.push({ ok: true });
  const discoveredMask = await loadDiscoveredBubbleMaskUrl(false, null, "page one");
  assert.ok(discoveredMask?.endsWith("/result/page%20one/bubble_mask.png"));
  assert.equal(requests[0].init?.method, "HEAD");
  assert.equal(requests[0].init?.cache, "no-store");

  const requestCount = requests.length;
  assert.equal(await loadDiscoveredBubbleMaskUrl(false, "/known-mask.png", "page one"), null);
  assert.equal(await loadDiscoveredBubbleMaskUrl(false, null, null), null);
  assert.equal(requests.length, requestCount);

  // Original image discovering panel_detections.json fallback when bubble_mask.png is missing:
  responses.push({ ok: false }, { ok: true });
  const discoveredPanel = await loadDiscoveredBubbleMaskUrl(true, null, "page original");
  assert.ok(discoveredPanel?.endsWith("/panel_detections.json"));

  const nextRequestCount = requests.length;
  const manifest = { folder: "page-one" };
  responses.push({ ok: false }, { ok: true, body: manifest });
  assert.deepEqual(await loadPipelineManifest("page one"), manifest);
  assert.ok(requests[nextRequestCount].url.endsWith("/pipeline-runs/page%20one/manifest"));
  assert.ok(requests[nextRequestCount + 1].url.endsWith("/result/page%20one/pipeline_manifest.json"));

  const detail = { translator: "deepseek" };
  const audit = { regions: [{ id: "region-1" }] };
  responses.push({ ok: true, body: detail }, { ok: true, body: audit });
  assert.deepEqual(await loadTranslationArtifacts("page one", "translated"), [detail, audit]);
  assert.deepEqual(await loadTranslationArtifacts("page one", "original"), [null, null]);
  assert.equal(requests.length, nextRequestCount + 4);
} finally {
  globalThis.fetch = originalFetch;
}

console.log("page detail artifact loading contracts passed");
