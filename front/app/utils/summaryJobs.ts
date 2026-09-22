import type { SummaryJob } from "@/types";
import { apiUrl } from "./api";

export async function fetchSummaryJobs(signal?: AbortSignal): Promise<SummaryJob[]> {
  const response = await fetch(apiUrl("/api/results/group/summary/jobs"), { signal, cache: "no-store" });
  if (!response.ok) throw new Error("Could not load summary jobs.");
  const payload = await response.json();
  return Array.isArray(payload) ? payload as SummaryJob[] : [];
}

export async function dismissSummaryJob(job: Pick<SummaryJob, "groupId" | "title">): Promise<void> {
  const response = await fetch(apiUrl("/api/results/group/summary/dismiss"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ groupId: job.groupId || undefined, mangaTitle: job.title }),
  });
  if (!response.ok) throw new Error("Could not clear summary job.");
}

export async function retrySummaryJob(
  job: Pick<SummaryJob, "groupId" | "title">,
  summaryModel: string,
  refreshText = false,
): Promise<void> {
  const response = await fetch(apiUrl("/api/results/group/summary"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      groupId: job.groupId || undefined,
      mangaTitle: job.title,
      summaryModel,
      regenerate: true,
      refreshText,
    }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Could not retry summary.");
  }
}

export async function pauseSummaryJob(job: Pick<SummaryJob, "groupId" | "title">): Promise<void> {
  const response = await fetch(apiUrl("/api/results/group/summary/pause"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ groupId: job.groupId || undefined, mangaTitle: job.title }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Could not pause summary.");
  }
}

export async function resumeSummaryJob(job: Pick<SummaryJob, "groupId" | "title">): Promise<void> {
  const response = await fetch(apiUrl("/api/results/group/summary/resume"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ groupId: job.groupId || undefined, mangaTitle: job.title }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Could not resume summary.");
  }
}

export async function stopSummaryJob(job: Pick<SummaryJob, "groupId" | "title">): Promise<void> {
  const response = await fetch(apiUrl("/api/results/group/summary/stop"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ groupId: job.groupId || undefined, mangaTitle: job.title }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Could not stop summary.");
  }
}

export const summaryJobProgress = (job: Pick<SummaryJob, "status" | "jobProgress">): number =>
  Math.max(0, Math.min(100, job.jobProgress ?? (job.status === "ready" ? 100 : 0)));

