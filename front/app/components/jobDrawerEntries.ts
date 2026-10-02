import type { MangaImportJob } from "@/features/upload/mangaImportJobs";
import type { SummaryJob, TranslationBatch } from "@/types";
import { mangaImportJobSection } from "./MangaImportJobRow";

export type JobSection = "active" | "queued" | "attention" | "completed";

export type JobEntry =
  | { type: "batch"; value: TranslationBatch }
  | { type: "summary"; value: SummaryJob }
  | { type: "manga-import"; value: MangaImportJob };

const jobFirstSeenTimestamps = new Map<string, number>();

export const getJobCreationTimestamp = (entry: JobEntry): number => {
  if (entry.type === "batch") {
    const batch = entry.value;
    const added = batch.addedAt instanceof Date
      ? batch.addedAt.getTime()
      : typeof batch.addedAt === "number"
      ? batch.addedAt
      : typeof batch.addedAt === "string"
      ? Date.parse(batch.addedAt)
      : 0;
    if (!Number.isNaN(added) && added > 0) return added;
    const updated = batch.updatedAt instanceof Date
      ? batch.updatedAt.getTime()
      : typeof batch.updatedAt === "number"
      ? batch.updatedAt
      : typeof batch.updatedAt === "string"
      ? Date.parse(batch.updatedAt)
      : 0;
    if (!Number.isNaN(updated) && updated > 0) return updated;
    return 0;
  }
  if (entry.type === "manga-import") {
    const created = Date.parse(entry.value.createdAt);
    return Number.isNaN(created) ? 0 : created;
  }
  const job = entry.value;
  if (job.createdAt) {
    const created = Date.parse(job.createdAt);
    if (!Number.isNaN(created) && created > 0) return created;
  }
  const existingFirstSeen = jobFirstSeenTimestamps.get(job.id);
  if (existingFirstSeen !== undefined) return existingFirstSeen;
  const initial = job.updatedAt ? Date.parse(job.updatedAt) : Date.now();
  const timestamp = !Number.isNaN(initial) && initial > 0 ? initial : Date.now();
  jobFirstSeenTimestamps.set(job.id, timestamp);
  return timestamp;
};

export const getJobCompletionTimestamp = (entry: JobEntry): number => {
  if (entry.type === "batch") {
    const updated = entry.value.updatedAt instanceof Date
      ? entry.value.updatedAt.getTime()
      : typeof entry.value.updatedAt === "number"
      ? entry.value.updatedAt
      : typeof entry.value.updatedAt === "string"
      ? Date.parse(entry.value.updatedAt)
      : 0;
    return !Number.isNaN(updated) && updated > 0 ? updated : getJobCreationTimestamp(entry);
  }
  if (entry.type === "manga-import") {
    const updated = entry.value.updatedAt ? Date.parse(entry.value.updatedAt) : 0;
    return !Number.isNaN(updated) && updated > 0 ? updated : getJobCreationTimestamp(entry);
  }
  const updated = entry.value.updatedAt ? Date.parse(entry.value.updatedAt) : 0;
  return !Number.isNaN(updated) && updated > 0 ? updated : getJobCreationTimestamp(entry);
};

export const isJobPriority = (entry: JobEntry): boolean =>
  entry.type === "batch" && Boolean(entry.value.priority);

export const sortJobEntries = (entries: JobEntry[], section: JobSection): JobEntry[] =>
  entries.slice().sort((a, b) => {
    const priorityDiff = (isJobPriority(b) ? 1 : 0) - (isJobPriority(a) ? 1 : 0);
    if (priorityDiff !== 0) return priorityDiff;
    const timeDiff = section === "completed"
      ? getJobCompletionTimestamp(b) - getJobCompletionTimestamp(a)
      : getJobCreationTimestamp(b) - getJobCreationTimestamp(a);
    return timeDiff || b.value.id.localeCompare(a.value.id);
  });

export const batchSection = (batch: TranslationBatch): JobSection => {
  if (batch.status === "error" || (batch.status === "completed" && Boolean(batch.failedCount))) return "attention";
  if (batch.status === "completed") return "completed";
  if (batch.status === "waiting" || batch.status === "paused") return "queued";
  return "active";
};

export const summarySection = (job: SummaryJob): JobSection =>
  job.status === "error" ? "attention" : job.status === "ready" ? "completed" : job.status === "queued" || job.status === "paused" ? "queued" : "active";

export { mangaImportJobSection };

export const sectionLabels: Record<JobSection, string> = {
  active: "Active",
  queued: "Queued / Paused",
  attention: "Needs attention",
  completed: "Completed",
};

export const shouldCloseJobsDrawer = (eventTarget: EventTarget | null, drawer: HTMLElement | null): boolean =>
  Boolean(drawer && eventTarget && drawer.contains(eventTarget as Node));
