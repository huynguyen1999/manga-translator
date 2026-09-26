import assert from "node:assert/strict";
import type { Dispatch, SetStateAction } from "react";
import type { FileStatus, StudioFile, TranslationBatch, TranslationSettings } from "@/types";
import { createTranslationSubmissionActions } from "./translationSubmission";

type State = {
  files: StudioFile[];
  selectedFiles: Set<string>;
  pendingTranslationTargets: StudioFile[];
  excludedColorFiles: Set<string>;
  autoDetectedColorFiles: Set<string>;
  fileStatuses: Map<string, FileStatus>;
  isGroupModalOpen: boolean;
  translationBatchError: string | null;
  currentMangaTitle: string;
  translationBatches: TranslationBatch[];
  isJobsOpen: boolean;
};

const submittedFiles: StudioFile[] = ["page-1", "page-2"].map((id, index) => ({
  id,
  file: new File([id], `${id}.png`, { type: "image/png" }),
  sourcePath: `${id}.png`,
  addedAt: index,
  dropOrder: index,
}));
const remainingFile: StudioFile = {
  id: "remaining",
  file: new File(["remaining"], "remaining.png", { type: "image/png" }),
  sourcePath: "remaining.png",
  addedAt: 3,
  dropOrder: 3,
};
const initialBatch = {
  id: "existing",
  addedAt: new Date(0),
  mangaTitle: "Existing",
  settings: {} as TranslationSettings,
  items: [],
  totalItems: 0,
  completedCount: 0,
  status: "waiting",
} as TranslationBatch;
const state: State = {
  files: [...submittedFiles, remainingFile],
  selectedFiles: new Set(submittedFiles.map(({ id }) => id)),
  pendingTranslationTargets: [],
  excludedColorFiles: new Set([submittedFiles[0].id]),
  autoDetectedColorFiles: new Set([submittedFiles[1].id]),
  fileStatuses: new Map([...submittedFiles, remainingFile].map(({ id }) => [id, {
    status: "upload",
    progress: null,
    queuePos: null,
    result: null,
    error: null,
  } satisfies FileStatus])),
  isGroupModalOpen: false,
  translationBatchError: "old error",
  currentMangaTitle: "Previous title",
  translationBatches: [initialBatch],
  isJobsOpen: false,
};

const update = <T,>(action: SetStateAction<T>, current: T): T =>
  typeof action === "function" ? (action as (previous: T) => T)(current) : action;
const setFiles: Dispatch<SetStateAction<StudioFile[]>> = (action) => { state.files = update(action, state.files); };
const setSelectedFiles: Dispatch<SetStateAction<Set<string>>> = (action) => { state.selectedFiles = update(action, state.selectedFiles); };
const setPendingTranslationTargets: Dispatch<SetStateAction<StudioFile[]>> = (action) => { state.pendingTranslationTargets = update(action, state.pendingTranslationTargets); };
const setExcludedColorFiles: Dispatch<SetStateAction<Set<string>>> = (action) => { state.excludedColorFiles = update(action, state.excludedColorFiles); };
const setAutoDetectedColorFiles: Dispatch<SetStateAction<Set<string>>> = (action) => { state.autoDetectedColorFiles = update(action, state.autoDetectedColorFiles); };
const setFileStatuses: Dispatch<SetStateAction<Map<string, FileStatus>>> = (action) => { state.fileStatuses = update(action, state.fileStatuses); };
const setIsGroupModalOpen: Dispatch<SetStateAction<boolean>> = (action) => { state.isGroupModalOpen = update(action, state.isGroupModalOpen); };
const setTranslationBatchError: Dispatch<SetStateAction<string | null>> = (action) => { state.translationBatchError = update(action, state.translationBatchError); };
const setCurrentMangaTitle: Dispatch<SetStateAction<string>> = (action) => { state.currentMangaTitle = update(action, state.currentMangaTitle); };
const setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>> = (action) => { state.translationBatches = update(action, state.translationBatches); };
const setIsJobsOpen: Dispatch<SetStateAction<boolean>> = (action) => { state.isJobsOpen = update(action, state.isJobsOpen); };

const confirmingRef = { current: false };
const baseSettings: TranslationSettings = {
  detectionResolution: "2048",
  textDetector: "default",
  renderTextDirection: "auto",
  translator: "deepseek",
  targetLanguage: "ENG",
  inpaintingSize: "2048",
  customUnclipRatio: 2.3,
  customBoxThreshold: 0.5,
  maskDilationOffset: 20,
  inpainter: "default",
};
const events: string[] = [];
const persisted: TranslationBatch[] = [];
const resumed: TranslationBatch[] = [];
const failed: unknown[] = [];

const actionsForCurrentRender = () => createTranslationSubmissionActions({
  files: state.files,
  selectedFiles: state.selectedFiles,
  pendingTranslationTargets: state.pendingTranslationTargets,
  excludedColorFiles: state.excludedColorFiles,
  autoDetectedColorFiles: state.autoDetectedColorFiles,
  isConfirmingTranslationRef: confirmingRef,
  setFiles,
  setSelectedFiles,
  setPendingTranslationTargets,
  setIsGroupModalOpen,
  setTranslationBatchError,
  setExcludedColorFiles,
  setAutoDetectedColorFiles,
  setFileStatuses,
  setCurrentMangaTitle,
  setTranslationBatches,
  setIsJobsOpen,
  getCurrentSettings: () => baseSettings,
  persistBatch: async (batch) => { events.push("persist"); persisted.push(batch); },
  resumeUpload: async (batch) => { events.push("resume"); resumed.push(batch); },
  failUpload: async (_batch, error) => { failed.push(error); },
});

actionsForCurrentRender().handleSubmit();
assert.equal(state.translationBatchError, null);
assert.equal(state.isGroupModalOpen, true);
assert.deepEqual(state.pendingTranslationTargets.map(({ id }) => id), ["page-1", "page-2"]);

const storyPlan = {
  enabled: true,
  autoDetect: false,
  mergeAllPages: false,
  archives: [],
  segments: [{ id: "segment-1", startPage: 1, endPage: 2 }],
};
await actionsForCurrentRender().handleConfirmGroup({
  title: "  New group  ",
  groupId: "group-1",
  isNewGroup: true,
}, storyPlan);

assert.equal(state.isGroupModalOpen, false);
assert.deepEqual(state.pendingTranslationTargets, []);
assert.equal(state.currentMangaTitle, "New group");
assert.deepEqual(state.files.map(({ id }) => id), ["remaining"]);
assert.deepEqual([...state.selectedFiles], []);
assert.deepEqual([...state.excludedColorFiles], []);
assert.deepEqual([...state.autoDetectedColorFiles], []);
assert.deepEqual([...state.fileStatuses.keys()], ["remaining"]);
assert.equal(state.isJobsOpen, true);
assert.equal(state.translationBatches.length, 2);

const submittedBatch = state.translationBatches[0];
assert.equal(submittedBatch.kind, "translation");
assert.equal(submittedBatch.mangaTitle, "New group");
assert.equal(submittedBatch.mangaGroupId, "group-1");
assert.equal(submittedBatch.isNewGroup, true);
assert.equal(submittedBatch.status, "uploading");
assert.equal(submittedBatch.settings.storyPlan, storyPlan);
assert.deepEqual(submittedBatch.items.map(({ excludeColor, isAutoColorDetected }) => [excludeColor, isAutoColorDetected]), [
  [true, false],
  [false, true],
]);
assert.deepEqual(events, ["persist", "resume"]);
assert.deepEqual(persisted, [submittedBatch]);
assert.deepEqual(resumed, [submittedBatch]);
assert.deepEqual(failed, []);
assert.equal(confirmingRef.current, false);

console.log("translation submission contracts passed");
