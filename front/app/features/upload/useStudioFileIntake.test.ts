import assert from "node:assert/strict";
import React from "react";
import type { SetStateAction } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { StudioFile } from "@/types";
import { useStudioFileIntake } from "./useStudioFileIntake";

const dropOrderRef = { current: 0 };
let filesUpdate: SetStateAction<StudioFile[]> | undefined;
let selectedUpdate: SetStateAction<Set<string>> | undefined;
let mangaTitleUpdate: SetStateAction<string> | undefined;
const colorChecks: StudioFile[][] = [];
let intake!: ReturnType<typeof useStudioFileIntake>;

function Harness() {
  intake = useStudioFileIntake({
    files: [],
    setFiles: (update) => { filesUpdate = update; },
    setSelectedFiles: (update) => { selectedUpdate = update; },
    setCurrentMangaTitle: (update) => { mangaTitleUpdate = update; },
    checkColorForFiles: async (files) => { colorChecks.push(files); },
    studioDropOrderRef: dropOrderRef,
  });
  return null;
}

renderToStaticMarkup(React.createElement(Harness));

const applyUpdate = <T,>(update: SetStateAction<T> | undefined, value: T): T => {
  assert.ok(update);
  return typeof update === "function"
    ? (update as (current: T) => T)(value)
    : update;
};

await intake.processIncomingFiles([new File(["page"], "page-1.png", { type: "image/png" })]);
const loadedFiles = applyUpdate(filesUpdate, []);
assert.equal(loadedFiles.length, 1);
assert.equal(loadedFiles[0].file.name, "page-1.png");
assert.equal(loadedFiles[0].sourcePath, "page-1.png");
assert.equal(loadedFiles[0].dropOrder, 2);
assert.deepEqual([...applyUpdate(selectedUpdate, new Set())], [loadedFiles[0].id]);
assert.equal(applyUpdate(mangaTitleUpdate, ""), "page-1");
assert.deepEqual(colorChecks, [loadedFiles]);
assert.equal(dropOrderRef.current, 2);

console.log("studio file intake contracts passed");
