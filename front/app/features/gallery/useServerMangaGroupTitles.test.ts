import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { useServerMangaGroupTitles } from "./useServerMangaGroupTitles";

let actions!: ReturnType<typeof useServerMangaGroupTitles>;
function Harness() {
  actions = useServerMangaGroupTitles();
  return null;
}
renderToStaticMarkup(React.createElement(Harness));

const requests: string[] = [];
let resolveFirstPage!: (response: Response) => void;
const originalFetch = globalThis.fetch;
globalThis.fetch = (async (input) => {
  requests.push(String(input));
  if (requests.length === 1) {
    return await new Promise<Response>((resolve) => { resolveFirstPage = resolve; });
  }
  return {
    ok: true,
    json: async () => ({ groups: [{ title: "Beta" }, { title: " Alpha " }], nextOffset: null }),
  } as Response;
}) as typeof fetch;

try {
  const first = actions.loadAllServerGroupTitles();
  const duplicate = actions.loadAllServerGroupTitles();
  assert.equal(requests.length, 1, "concurrent requests share the active pagination run");
  resolveFirstPage({
    ok: true,
    json: async () => ({ groups: [{ title: " Alpha " }, { title: "Ungrouped" }, { title: " " }], nextOffset: 500 }),
  } as Response);

  assert.deepEqual(await first, ["Alpha", "Beta"]);
  assert.deepEqual(await duplicate, ["Alpha", "Beta"]);
  assert.deepEqual(requests.map((url) => new URL(url, "http://studio.test").search), [
    "?limit=500&offset=0",
    "?limit=500&offset=500",
  ]);
} finally {
  globalThis.fetch = originalFetch;
}

console.log("server manga group title contracts passed");
