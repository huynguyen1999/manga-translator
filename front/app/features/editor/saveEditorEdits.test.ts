import assert from "node:assert/strict";
import type { EditableTextBlock } from "@/types";
import { saveEditorEdits } from "./saveEditorEdits";

let requestUrl = "";
let requestInit: RequestInit | undefined;
const blocks = [{ id: "bubble-1", translation: "Hello" }] as EditableTextBlock[];
const result = await saveEditorEdits("page-1", blocks, "data:image/png;base64,AA==", async (url, init) => {
  requestUrl = String(url);
  requestInit = init;
  return { ok: true, json: async () => ({ reviewStatus: "pending" }) } as Response;
});

assert.ok(requestUrl.endsWith("/result/page-1/save_edits"));
assert.equal(requestInit?.method, "POST");
assert.deepEqual(JSON.parse(String(requestInit?.body)), {
  text_regions: blocks,
  final_image_base64: "data:image/png;base64,AA==",
});
assert.deepEqual(result, { reviewStatus: "pending" });

await assert.rejects(
  saveEditorEdits("page-1", blocks, "image", async () => (
    { ok: false, status: 409 } as Response
  )),
  /Server returned 409/,
);
