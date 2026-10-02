import type { Dispatch, SetStateAction } from "react";
import { apiUrl } from "@/utils/api";

export type MangaImportJobStatus = "queued" | "processing" | "completed" | "failed";

export interface MangaImportJob {
  id: string;
  title: string;
  status: MangaImportJobStatus;
  createdAt: string;
  updatedAt?: string;
  groupId?: string | null;
  fileCount?: number;
  totalPages?: number | null;
  processedPages?: number;
  progress?: number;
  error?: string | null;
}

type UploadError = Error & { status?: number };
type Fetcher = typeof fetch;
type PollWindow = Pick<Window, "addEventListener" | "removeEventListener" | "setInterval" | "clearInterval">;
type PollDocument = Pick<Document, "visibilityState" | "addEventListener" | "removeEventListener">;

export interface MangaImportJobWatcher {
  refresh: () => Promise<MangaImportJob[]>;
  stop: () => void;
}

export const watchMangaImportJobs = (
  refresh: () => Promise<MangaImportJob[]>,
  browserWindow: PollWindow,
  browserDocument: PollDocument,
): MangaImportJobWatcher => {
  let timer: number | null = null;
  let stopped = false;
  const stopPolling = () => {
    if (timer === null) return;
    browserWindow.clearInterval(timer);
    timer = null;
  };
  const schedulePolling = () => {
    if (stopped || timer !== null || browserDocument.visibilityState !== "visible") return;
    timer = browserWindow.setInterval(refreshAndSchedule, 2000);
  };
  const refreshAndSchedule = (): Promise<MangaImportJob[]> => {
    stopPolling();
    if (stopped || browserDocument.visibilityState !== "visible") return refresh();
    const request = refresh();
    void request.then((jobs) => {
      if (jobs.some((job) => job.status === "queued" || job.status === "processing")) {
        schedulePolling();
      }
    }).catch(schedulePolling);
    return request;
  };
  const onVisibilityChange = () => {
    if (browserDocument.visibilityState === "visible") void refreshAndSchedule().catch(() => {});
    else stopPolling();
  };
  if (browserDocument.visibilityState === "visible") void refreshAndSchedule().catch(() => {});
  browserDocument.addEventListener("visibilitychange", onVisibilityChange);
  const onReconnect = () => {
    if (browserDocument.visibilityState === "visible") void refreshAndSchedule().catch(() => {});
  };
  browserWindow.addEventListener("focus", onReconnect);
  browserWindow.addEventListener("online", onReconnect);
  const stop = () => {
    stopped = true;
    stopPolling();
    browserDocument.removeEventListener("visibilitychange", onVisibilityChange);
    browserWindow.removeEventListener("focus", onReconnect);
    browserWindow.removeEventListener("online", onReconnect);
  };
  return { refresh: refreshAndSchedule, stop };
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null;

const isMangaImportJobStatus = (value: unknown): value is MangaImportJobStatus =>
  value === "queued" || value === "processing" || value === "completed" || value === "failed";

const timestampString = (value: unknown): string | null => {
  if (typeof value === "number" && Number.isFinite(value)) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  }
  return typeof value === "string" ? value : null;
};

const parseMangaImportJob = (value: unknown): MangaImportJob | null => {
  if (!isRecord(value)) return null;
  const { id, title, status } = value;
  const createdAt = timestampString(value.createdAt);
  if (typeof id !== "string" || typeof title !== "string" || createdAt === null || !isMangaImportJobStatus(status)) {
    return null;
  }

  const job: MangaImportJob = { id, title, status, createdAt };
  const updatedAt = timestampString(value.updatedAt);
  if (updatedAt !== null) job.updatedAt = updatedAt;
  if (typeof value.groupId === "string" || value.groupId === null) job.groupId = value.groupId;
  if (isRecord(value.group) && typeof value.group.id === "string") job.groupId = value.group.id;
  if (typeof value.fileCount === "number") job.fileCount = value.fileCount;
  if (typeof value.totalPages === "number" || value.totalPages === null) job.totalPages = value.totalPages;
  if (typeof value.processedPages === "number") job.processedPages = value.processedPages;
  if (typeof value.progress === "number") job.progress = Math.min(100, Math.max(0, value.progress));
  if (typeof value.error === "string" || value.error === null) job.error = value.error;
  return job;
};

const responseError = async (response: Response, fallback: string): Promise<UploadError> => {
  const payload: unknown = await response.json().catch(() => null);
  const message = isRecord(payload) && typeof payload.detail === "string" ? payload.detail : fallback;
  const error: UploadError = new Error(message);
  error.status = response.status;
  return error;
};

export const importOriginalManga = async (
  files: Array<{ file: File; sourcePath: string }>,
  mangaTitle: string,
  onProgress?: (progress: number) => void,
  groupId?: string | null,
  isNewGroup?: boolean,
  clientUploadId?: string,
): Promise<MangaImportJob> => {
  const form = new FormData();
  form.append("mangaTitle", mangaTitle.trim());
  if (groupId) {
    form.append("groupId", groupId);
    form.append("mangaGroupId", groupId);
  }
  if (isNewGroup !== undefined) form.append("isNewGroup", isNewGroup ? "true" : "false");
  if (clientUploadId) form.append("clientUploadId", clientUploadId);
  form.append("pageMetadata", JSON.stringify(files.map((entry) => ({
    originalName: entry.file.name,
    sourcePath: entry.sourcePath,
  }))));
  files.forEach((entry) => form.append("files", entry.file, entry.file.name));

  return new Promise<MangaImportJob>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", apiUrl("/api/manga-import-jobs"));
    request.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress?.(Math.min(100, Math.round((event.loaded / event.total) * 100)));
      }
    };
    request.onerror = () => reject(new Error("Could not reach the import server"));
    request.onabort = () => reject(new Error("Manga import was cancelled"));
    request.onload = () => {
      let payload: unknown;
      try {
        payload = JSON.parse(request.responseText);
      } catch {
        payload = null;
      }
      if (request.status < 200 || request.status >= 300) {
        const detail = isRecord(payload) && typeof payload.detail === "string" ? payload.detail : `Import failed (${request.status})`;
        const error: UploadError = new Error(detail);
        error.status = request.status;
        reject(error);
        return;
      }
      const job = isRecord(payload) ? parseMangaImportJob(payload.job) : null;
      if (!job) {
        reject(new Error("Import server accepted the upload without returning a job"));
        return;
      }
      resolve(job);
    };
    request.send(form);
  });
};

export const fetchMangaImportJobs = async (fetcher: Fetcher = fetch): Promise<MangaImportJob[]> => {
  const response = await fetcher(apiUrl("/api/manga-import-jobs"));
  if (!response.ok) throw await responseError(response, "Could not load manga imports.");
  const payload: unknown = await response.json();
  if (!isRecord(payload) || !Array.isArray(payload.jobs)) throw new Error("Import server returned an invalid job list");
  const jobs = payload.jobs.map(parseMangaImportJob);
  if (jobs.some((job) => job === null)) throw new Error("Import server returned an invalid job");
  return jobs.filter((job): job is MangaImportJob => job !== null);
};

export const retryMangaImportJobRequest = async (id: string, fetcher: Fetcher = fetch): Promise<void> => {
  const response = await fetcher(apiUrl(`/api/manga-import-jobs/${encodeURIComponent(id)}/retry`), { method: "POST" });
  if (!response.ok) throw await responseError(response, "Could not retry manga import.");
};

export const dismissMangaImportJobRequest = async (id: string, fetcher: Fetcher = fetch): Promise<void> => {
  const response = await fetcher(apiUrl(`/api/manga-import-jobs/${encodeURIComponent(id)}`), { method: "DELETE" });
  if (!response.ok) throw await responseError(response, "Could not dismiss manga import.");
};

export const upsertMangaImportJob = (
  setJobs: Dispatch<SetStateAction<MangaImportJob[]>>,
  job: MangaImportJob,
) => setJobs((jobs) => [job, ...jobs.filter((candidate) => candidate.id !== job.id)]);
