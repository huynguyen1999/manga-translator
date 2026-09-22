import assert from "node:assert/strict";
import type { TranslationBatch } from "@/types";
import {
  findActiveTranslationBatch,
  findNextTranslationBatch,
  getTerminalBatchStatus,
} from "./translationScheduler";

const item = (id: string, status: "queued" | "processing" | "error") => ({
  id,
  file: new File(["image"], `${id}.png`, { type: "image/png" }),
  addedAt: new Date(0),
  status,
});

const base = {
  addedAt: new Date(0),
  mangaTitle: "Test",
  settings: {} as TranslationBatch["settings"],
  totalItems: 1,
  completedCount: 0,
  dismissed: false,
};

const waiting = { ...base, id: "waiting", items: [item("waiting", "queued")], status: "waiting" as const };
const active = { ...base, id: "active", items: [item("active", "processing")], status: "processing" as const };
const failed = { ...base, id: "failed", items: [item("failed", "error")], status: "error" as const };

assert.equal(findNextTranslationBatch([waiting, active])?.id, "waiting");
assert.equal(findActiveTranslationBatch([waiting, active])?.id, "active");
assert.equal(getTerminalBatchStatus(waiting), null);
assert.equal(getTerminalBatchStatus(failed), "error");

// When active batch is removed from process, waiting batch becomes next eligible batch
const afterActiveRemoved = [waiting, active].filter((b) => b.id !== "active");
assert.equal(findActiveTranslationBatch(afterActiveRemoved), undefined);
assert.equal(findNextTranslationBatch(afterActiveRemoved)?.id, "waiting");

console.log("translation scheduler checks passed");
