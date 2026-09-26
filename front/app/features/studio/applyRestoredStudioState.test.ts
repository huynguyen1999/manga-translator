import assert from "node:assert/strict";
import type { Dispatch, SetStateAction } from "react";
import type { FileStatus, StudioFile } from "@/types";
import type { RestoredStudioState } from "@/utils/fileStorage";
import { applyRestoredStudioState } from "./applyRestoredStudioState";

const apply = <T,>(update: SetStateAction<T>, current: T): T => (
  typeof update === "function"
    ? (update as (value: T) => T)(current)
    : update
);
const setter = <T,>(save: (update: SetStateAction<T>) => void): Dispatch<SetStateAction<T>> => save;
const status = (progress: string): FileStatus => ({
  status: "pending",
  progress,
  queuePos: null,
  result: null,
  error: null,
});
const currentFile: StudioFile = {
  id: "same",
  file: new File(["current"], "current.png"),
  sourcePath: "current.png",
  addedAt: 1,
  dropOrder: 1,
};
const restoredFile: StudioFile = {
  id: "same",
  file: new File(["restored"], "restored.png"),
  sourcePath: "restored.png",
  addedAt: 2,
  dropOrder: 4,
};
const restored: RestoredStudioState = {
  files: [],
  studioFiles: [restoredFile],
  fileStatuses: new Map([["same", status("restored")]]),
  selectedFiles: new Set(["same", "restored-selection"]),
  excludedColorFiles: new Set(["restored-excluded"]),
  autoDetectedColorFiles: new Set(["restored-auto"]),
  folderMap: new Map([["same", "restored-folder"]]),
  resultUrls: new Set(["restored-url"]),
};

let filesUpdate!: SetStateAction<StudioFile[]>;
let statusesUpdate!: SetStateAction<Map<string, FileStatus>>;
let selectedUpdate!: SetStateAction<Set<string>>;
let excludedUpdate!: SetStateAction<Set<string>>;
let autoUpdate!: SetStateAction<Set<string>>;
let hydratedUpdate!: SetStateAction<boolean>;
const folderMapRef = { current: new Map([["current", "folder"]]) };
const resultUrlsRef = { current: new Set(["current-url"]) };
const studioDropOrderRef = { current: 2 };

applyRestoredStudioState(restored, {
  setFiles: setter((update) => { filesUpdate = update; }),
  setFileStatuses: setter((update) => { statusesUpdate = update; }),
  setSelectedFiles: setter((update) => { selectedUpdate = update; }),
  setExcludedColorFiles: setter((update) => { excludedUpdate = update; }),
  setAutoDetectedColorFiles: setter((update) => { autoUpdate = update; }),
  setIsStudioHydrated: setter((update) => { hydratedUpdate = update; }),
  studioDropOrderRef,
  folderMapRef,
  resultUrlsRef,
});

const mergedFiles = apply(filesUpdate, [currentFile]);
assert.deepEqual(mergedFiles.map(({ id }) => id), ["same"]);
assert.equal(mergedFiles[0].file.name, "restored.png");
assert.equal(studioDropOrderRef.current, 4);
assert.equal(apply(statusesUpdate, new Map([["same", status("current")]])).get("same")?.progress, "current");
assert.deepEqual([...apply(selectedUpdate, new Set(["current-selection"]))], [
  "same",
  "restored-selection",
  "current-selection",
]);
assert.deepEqual([...apply(excludedUpdate, new Set())], ["restored-excluded"]);
assert.deepEqual([...apply(autoUpdate, new Set())], ["restored-auto"]);
assert.deepEqual([...folderMapRef.current], [["current", "folder"], ["same", "restored-folder"]]);
assert.deepEqual([...resultUrlsRef.current], ["current-url", "restored-url"]);
assert.equal(apply(hydratedUpdate, false), true);

console.log("studio state restoration contract passed");
