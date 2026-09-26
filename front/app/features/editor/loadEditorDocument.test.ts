import assert from "node:assert/strict";
import type { FinishedImage } from "@/types";
import { loadEditorDocument } from "./loadEditorDocument";

const requestedImages: string[] = [];
const background = { naturalWidth: 640, naturalHeight: 800 } as HTMLImageElement;
const image = {
  folder: "page-1",
  inpaintedUrl: "/clean.png",
  inputUrl: "/input.png",
  result: "/final.png",
  textRegionsUrl: "/regions.json",
} as FinishedImage;

const document = await loadEditorDocument(image, {
  decodeImage: async (url) => {
    const path = new URL(url).pathname;
    requestedImages.push(path);
    if (path !== "/input.png") throw new Error("image unavailable");
    return background;
  },
  fetcher: async () => ({
    ok: true,
    json: async () => [{ id: "block-1", translation: "Hello", x: 10, y: 20, width: 120, height: 60 }],
  }) as Response,
});

assert.deepEqual(requestedImages, [
  "/clean.png",
  "/result/page-1/inpainted.jpg",
  "/input.png",
]);
assert.equal(document.background?.kind, "input");
assert.deepEqual(document.imageSize, { width: 640, height: 800 });
assert.equal(document.blocks.length, 1);
assert.equal(document.blocks[0].translation, "Hello");
assert.match(document.backgroundNotice || "", /original page as a fallback/);
assert.equal(document.backgroundError, null);
document.cleanup();
