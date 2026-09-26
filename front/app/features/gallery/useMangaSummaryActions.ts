import { useCallback, useEffect } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { MangaSummary } from "@/types";
import { apiUrl } from "@/utils/api";
import { getMangaSummaryAvailability, isSummaryPending } from "@/utils/resultGallery";
import {
  pauseSummaryJob,
  resumeSummaryJob,
  stopSummaryJob,
  subscribeSummaryJobs,
} from "@/utils/summaryJobs";
import type { MangaSummaryModalState } from "./MangaSummaryModal";

export type SummaryAvailabilityState =
  | "loading"
  | "summarized"
  | "not-summarized"
  | "queued"
  | "generating"
  | "paused"
  | "stale"
  | "error"
  | "unavailable";

type MangaGroup = { id: string; title: string };
type SummaryAvailability = { title: string; state: SummaryAvailabilityState };

type MangaSummaryActionsOptions = {
  summaryModel: string;
  mangaGroups: MangaGroup[];
  summaryState: MangaSummaryModalState | null;
  setSummaryState: Dispatch<SetStateAction<MangaSummaryModalState | null>>;
  summaryAvailability: SummaryAvailability | null;
  setSummaryAvailability: Dispatch<SetStateAction<SummaryAvailability | null>>;
  summarizingTitle: string | null;
  setSummarizingTitle: Dispatch<SetStateAction<string | null>>;
  setSummaryCopied: Dispatch<SetStateAction<boolean>>;
};

export const useMangaSummaryActions = ({
  summaryModel,
  mangaGroups,
  summaryState,
  setSummaryState,
  summaryAvailability,
  setSummaryAvailability,
  summarizingTitle,
  setSummarizingTitle,
  setSummaryCopied,
}: MangaSummaryActionsOptions) => {
  const handleSummarize = async (title: string, regenerate = false, refreshText = false) => {
    if (summarizingTitle) return;
    const retainedSummary = summaryState?.title === title ? summaryState.data : null;
    setSummarizingTitle(title);
    setSummaryCopied(false);
    const groupId = mangaGroups.find((group) => group.title === title)?.id;

    try {
      let existing: MangaSummary | null = null;
      if (!regenerate) {
        const statusResponse = await fetch(apiUrl(`/api/results/group/summary?${groupId ? `groupId=${encodeURIComponent(groupId)}&` : ""}title=${encodeURIComponent(title)}`));
        const statusPayload = await statusResponse.json().catch(() => ({}));
        if (!statusResponse.ok) {
          throw new Error(statusPayload.detail || "Could not load the manga summary.");
        }
        existing = statusPayload as MangaSummary;
      }

      if (existing?.summary && !existing.stale && !regenerate) {
        setSummaryAvailability({ title, state: "summarized" });
        setSummaryState({ title, data: existing, loading: false, error: null });
        setSummarizingTitle(null);
        return;
      }

      setSummaryAvailability({ title, state: "generating" });
      if (summaryState?.title === title) {
        setSummaryState({ title, data: existing?.summary ? existing : retainedSummary, loading: true, error: null });
      }

      const response = await fetch(apiUrl("/api/results/group/summary"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          groupId: groupId || undefined,
          mangaTitle: title,
          summaryModel,
          regenerate: Boolean(regenerate || refreshText || existing?.stale),
          refreshText,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "The selected model could not generate a summary.");
      }
      const newSummary = payload as MangaSummary;

      if (isSummaryPending(newSummary)) {
        setSummaryAvailability({ title, state: getMangaSummaryAvailability(newSummary) });
        setSummaryState((previous) => {
          if (previous?.title === title) {
            return { title, data: newSummary.summary ? newSummary : previous.data, loading: true, error: null };
          }
          return previous;
        });
        return;
      }

      setSummaryAvailability({ title, state: getMangaSummaryAvailability(newSummary) });
      setSummaryState((previous) => {
        if (previous?.title === title) {
          return { title, data: newSummary, loading: false, error: null };
        }
        return previous;
      });
    } catch (error) {
      const errText = error instanceof Error ? error.message : "Could not generate a summary.";
      setSummaryAvailability({ title, state: "error" });
      setSummaryState((previous) => {
        if (previous?.title === title) {
          return { title, data: previous?.data || null, loading: false, error: errText };
        }
        return previous;
      });
    } finally {
      setSummarizingTitle(null);
    }
  };

  useEffect(() => {
    const summaryIsPending = summaryAvailability?.state === "queued" || summaryAvailability?.state === "generating" || summaryAvailability?.state === "paused";
    if (!summaryIsPending) return;
    const title = summaryAvailability.title;
    const groupId = summaryState?.title === title
      ? summaryState.data?.groupId || mangaGroups.find((group) => group.title === title)?.id
      : mangaGroups.find((group) => group.title === title)?.id;
    let fetchedTerminalSummary = false;
    return subscribeSummaryJobs((jobs) => {
      const job = jobs.find((candidate) =>
        groupId ? candidate.groupId === groupId : candidate.title === title,
      ) || jobs.find((candidate) => candidate.title === title);
      if (!job) return;

      if (job.status === "queued" || job.status === "generating" || job.status === "paused") {
        setSummaryAvailability({ title, state: job.status });
        setSummaryState((previous) => previous?.title === title
          ? {
              ...previous,
              data: {
                ...(previous.data ?? {
                  groupId: job.groupId,
                  mangaTitle: job.title,
                  summary: null,
                  stale: false,
                  pageCount: job.jobPageCount ?? 0,
                  textPageCount: job.jobPagesWithText ?? 0,
                }),
                jobStatus: job.status,
                jobError: job.jobError,
                jobUpdatedAt: job.updatedAt,
                jobStage: job.jobStage,
                jobProgress: job.jobProgress,
                jobMessage: job.jobMessage,
                jobCurrentPage: job.jobCurrentPage,
                jobStagePassedCount: job.jobStagePassedCount,
                jobPageCount: job.jobPageCount,
                jobPagesWithText: job.jobPagesWithText,
                jobExtractionRequired: job.jobExtractionRequired,
              },
              loading: true,
            }
          : previous);
        return;
      }

      if (job.status === "error") {
        setSummaryAvailability({ title, state: "error" });
        setSummaryState((previous) => previous?.title === title
          ? { ...previous, loading: false, error: job.jobError || "Could not generate a summary." }
          : previous);
        return;
      }

      if (!fetchedTerminalSummary) {
        fetchedTerminalSummary = true;
        const query = new URLSearchParams({ title });
        if (groupId) query.set("groupId", groupId);
        fetch(apiUrl(`/api/results/group/summary?${query.toString()}`), { cache: "no-store" })
          .then(async (response) => {
            if (!response.ok) throw new Error(`Summary status failed (${response.status})`);
            return await response.json() as MangaSummary;
          })
          .then((data) => {
            setSummaryAvailability({ title, state: getMangaSummaryAvailability(data) });
            setSummaryState((previous) => previous?.title === title
              ? { title, data, loading: false, error: null }
              : previous);
          })
          .catch(() => {
            setSummaryState((previous) => previous?.title === title
              ? { ...previous, loading: false, error: "Could not load the completed summary." }
              : previous);
          });
      }
    });
  }, [mangaGroups, summaryAvailability?.state, summaryAvailability?.title, summaryState?.data?.groupId, summaryState?.title]);

  const copySummary = async () => {
    const text = summaryState?.data?.summary;
    if (!text) return;
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
      }
      setSummaryCopied(true);
      window.setTimeout(() => setSummaryCopied(false), 1500);
    } catch {
      setSummaryState((previous) => previous ? { ...previous, error: "Could not copy the synopsis." } : previous);
    }
  };

  const handleViewSummary = useCallback(async (title: string) => {
    setSummaryCopied(false);
    setSummaryState({ title, data: null, loading: true, error: null });
    try {
      const groupId = mangaGroups.find((group) => group.title === title)?.id;
      const statusResponse = await fetch(apiUrl(`/api/results/group/summary?${groupId ? `groupId=${encodeURIComponent(groupId)}&` : ""}title=${encodeURIComponent(title)}`));
      const statusPayload = await statusResponse.json().catch(() => ({}));
      if (!statusResponse.ok) {
        throw new Error(statusPayload.detail || "Could not load the manga summary.");
      }
      const data = statusPayload as MangaSummary;
      setSummaryState({ title, data, loading: false, error: null });
    } catch (error) {
      const errText = error instanceof Error ? error.message : "Could not load the summary.";
      setSummaryState({ title, data: null, loading: false, error: errText });
    }
  }, [mangaGroups]);

  const handleResumeSummaryJob = useCallback(async () => {
    if (!summaryState) return;
    const { title } = summaryState;
    const groupId = summaryState.data?.groupId || mangaGroups.find((group) => group.title === title)?.id;
    await resumeSummaryJob({ groupId, title });
    setSummaryState((previous) => previous
      ? { ...previous, data: previous.data ? { ...previous.data, jobStatus: "generating" } : null }
      : null);
    setSummaryAvailability({ title, state: "generating" });
  }, [mangaGroups, setSummaryAvailability, setSummaryState, summaryState]);

  const handlePauseSummaryJob = useCallback(async () => {
    if (!summaryState) return;
    const { title } = summaryState;
    const groupId = summaryState.data?.groupId || mangaGroups.find((group) => group.title === title)?.id;
    await pauseSummaryJob({ groupId, title });
    setSummaryState((previous) => previous
      ? { ...previous, data: previous.data ? { ...previous.data, jobStatus: "paused" } : null }
      : null);
    setSummaryAvailability({ title, state: "paused" });
  }, [mangaGroups, setSummaryAvailability, setSummaryState, summaryState]);

  const handleStopSummaryJob = useCallback(async () => {
    if (!summaryState) return;
    const { title } = summaryState;
    const groupId = summaryState.data?.groupId || mangaGroups.find((group) => group.title === title)?.id;
    await stopSummaryJob({ groupId, title });
    setSummaryState(null);
    setSummaryAvailability({ title, state: "not-summarized" });
  }, [mangaGroups, setSummaryAvailability, setSummaryState, summaryState]);

  return {
    handleSummarize,
    handleViewSummary,
    copySummary,
    handleResumeSummaryJob,
    handlePauseSummaryJob,
    handleStopSummaryJob,
  };
};
