import assert from "node:assert/strict";
import type { Dispatch, SetStateAction } from "react";
import type { FileStatus, StudioFile } from "@/types";
import { createStudioFileSelectionActions } from "./studioFileSelection";

type State = {
  files: StudioFile[];
  selected: Set<string>;
  excluded: Set<string>;
  autoDetected: Set<string>;
  statuses: Map<string, FileStatus>;
};
const makeFile = (id: string): StudioFile => ({
  id,
  file: new File([id], `${id}.png`, { type: "image/png" }),
  sourcePath: `${id}.png`,
  addedAt: 1,
  dropOrder: 1,
});
const fileA = makeFile("a");
const fileB = makeFile("b");
const fileC = makeFile("c");
const state: State = {
  files: [fileA, fileB, fileC],
  selected: new Set(["a", "b"]),
  excluded: new Set(["a", "b"]),
  autoDetected: new Set(["b"]),
  statuses: new Map(["a", "b", "c"].map((id) => [id, {
    status: "upload",
    progress: null,
    queuePos: null,
    result: null,
    error: null,
  } satisfies FileStatus])),
};
const update = <T,>(action: SetStateAction<T>, current: T): T =>
  typeof action === "function" ? (action as (previous: T) => T)(current) : action;
const setFiles: Dispatch<SetStateAction<StudioFile[]>> = (action) => { state.files = update(action, state.files); };
const setSelected: Dispatch<SetStateAction<Set<string>>> = (action) => { state.selected = update(action, state.selected); };
const setExcluded: Dispatch<SetStateAction<Set<string>>> = (action) => { state.excluded = update(action, state.excluded); };
const setAutoDetected: Dispatch<SetStateAction<Set<string>>> = (action) => { state.autoDetected = update(action, state.autoDetected); };
const setStatuses: Dispatch<SetStateAction<Map<string, FileStatus>>> = (action) => { state.statuses = update(action, state.statuses); };
const lastSelectedRef = { current: { id: "a", wasSelected: true } as { id: string; wasSelected: boolean } | null };
const folderMapRef = { current: new Map([["a", "folder-a"], ["b", "folder-b"], ["c", "folder-c"]]) };
const resultUrlsRef = { current: new Set(["a", "b", "c"]) };

const actionsForCurrentRender = () => createStudioFileSelectionActions({
  files: state.files,
  selectedFiles: state.selected,
  fileStatuses: state.statuses,
  setFiles,
  setSelectedFiles: setSelected,
  setExcludedColorFiles: setExcluded,
  setAutoDetectedColorFiles: setAutoDetected,
  setFileStatuses: setStatuses,
  lastSelectedFileRef: lastSelectedRef,
  folderMapRef,
  resultUrlsRef,
});

actionsForCurrentRender().removeSelectedFiles();
assert.deepEqual(state.files.map(({ id }) => id), ["c"]);
assert.deepEqual([...state.selected], []);
assert.deepEqual([...state.excluded], []);
assert.deepEqual([...state.autoDetected], []);
assert.deepEqual([...state.statuses.keys()], ["c"]);
assert.deepEqual([...folderMapRef.current.keys()], ["c"]);
assert.deepEqual([...resultUrlsRef.current], ["c"]);
assert.equal(lastSelectedRef.current, null);

actionsForCurrentRender().selectAllFiles();
assert.deepEqual([...state.selected], ["c"]);
assert.equal(lastSelectedRef.current, null);
actionsForCurrentRender().toggleFileSelection("c");
assert.deepEqual([...state.selected], []);
assert.deepEqual(lastSelectedRef.current, { id: "c", wasSelected: false });
actionsForCurrentRender().deselectAllFiles();
assert.deepEqual([...state.selected], []);
assert.equal(lastSelectedRef.current, null);

actionsForCurrentRender().removeFile("c");
assert.deepEqual(state.files, []);
assert.deepEqual([...state.statuses.keys()], []);
assert.equal(folderMapRef.current.has("c"), false);
assert.equal(resultUrlsRef.current.has("c"), false);

console.log("studio file selection contracts passed");
