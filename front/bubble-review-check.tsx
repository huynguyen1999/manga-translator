// Run with npm run test:preview, then open /bubble-review-check.html.
// Exercises the real editor without a translation service or user images.
import React from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { MangaEditorModal } from "./app/components/MangaEditorModal";
import "./app/app.css";

const sample = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="500" height="500"><rect width="500" height="500" fill="white"/><rect x="90" y="50" width="320" height="380" rx="80" fill="none" stroke="black" stroke-width="5"/><text x="160" y="220" font-size="24">ORIGINAL</text></svg>');
let blocks = [{id:"bubble_0", x:130, y:90, width:240, height:300, translation:"Translated dialogue", original_text:"ORIGINAL", font_size:24, direction:"h", review_required:true, review_reason:"uncertain_cleanup", group_members:["source_0", "source_1"]}];
let saved: any;
window.alert = () => {};
const realFetch = window.fetch.bind(window);
window.fetch = async (input, options) => {
  const url = String(input);
  if (url.includes("text_regions.json")) return new Response(JSON.stringify(blocks));
  if (url.includes("save_edits")) {
    saved = JSON.parse(String(options?.body));
    blocks = saved.text_regions;
    return new Response(JSON.stringify({status:"success"}));
  }
  return realFetch(input, options);
};
const root = createRoot(document.getElementById("root")!);
function mount(key: number) {
  root.render(<MangaEditorModal key={key} image={{id:"review", originalName:"review.png", folder:"review-fixture", url:sample, inpaintedUrl:sample, inputUrl:sample} as any} onClose={() => {}} />);
}
mount(0);
async function check() {
  const until = async (predicate: () => boolean) => {
    const deadline = Date.now() + 10000;
    while (!predicate()) {
      if (Date.now() > deadline) throw Error("Bubble review check timed out");
      await new Promise(resolve => setTimeout(resolve, 30));
    }
  };
  const button = (label: string) => [...document.querySelectorAll("button")].find(b => b.textContent?.trim() === label)!;
  await until(() => document.body.textContent!.includes("Needs editing"));
  if (!document.querySelector(".whitespace-pre-wrap")?.textContent?.replace(/\s+/g, " ").includes("Translated dialogue")) throw Error("Flagged translation is not visible in editor");
  // Export through the normal Save action; review status must be preserved for review later.
  button("Save draft").click();
  await until(() => Boolean(saved));
  if (!saved.text_regions[0].review_required) throw Error("Save lost review status");
  const exported = new Image(); exported.src = saved.final_image_base64;
  await exported.decode();
  const original = new Image(); original.src = sample; await original.decode();
  const pixels = (image: HTMLImageElement) => {
    const canvas = document.createElement("canvas"); canvas.width=500; canvas.height=500;
    const ctx = canvas.getContext("2d")!; ctx.drawImage(image,0,0,500,500);
    return ctx.getImageData(0,0,500,500).data;
  };
  const expected = pixels(original);
  if (pixels(exported).every((v,i) => v === expected[i])) throw Error("Flagged translation was not rendered in draft export");
  flushSync(() => root.render(null));
  mount(1);
  await until(() => document.body.textContent!.includes("Needs editing"));
  const region = document.querySelector(".cursor-move") as HTMLElement;
  region.dispatchEvent(new PointerEvent("pointerdown", {bubbles:true, pointerId:1}));
  await until(() => Boolean(button("Replace original with this translation")));
  button("Replace original with this translation").click();
  await until(() => !document.body.textContent!.includes("Needs editing"));
  if (!document.querySelector(".whitespace-pre-wrap")?.textContent?.replace(/\s+/g, " ").includes("Translated dialogue")) throw Error("Explicit replacement did not render");
  const approveButton = button("Save & approve page") as HTMLButtonElement;
  if (approveButton.disabled) throw Error("Approve action stayed disabled after review resolution");
  document.title = "PASS: bubble review";
}
check().catch(error => { document.title = "FAIL: bubble review"; console.error(error); });
