import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { SummaryJob } from "@/types";
import {
  dismissSummaryJob,
  pauseSummaryJob,
  resumeSummaryJob,
  retrySummaryJob,
  stopSummaryJob,
} from "@/utils/summaryJobs";

export const useSummaryJobActions = (setSummaryJobs: Dispatch<SetStateAction<SummaryJob[]>>) => {
  const handleDismissSummaryJob = useCallback(async (job: SummaryJob) => {
    await dismissSummaryJob(job);
    setSummaryJobs((current) => current.filter((candidate) => candidate.id !== job.id));
  }, []);

  const handleRetrySummaryJob = useCallback(async (job: SummaryJob, summaryModel: string, refreshText = false) => {
    await retrySummaryJob(job, summaryModel, refreshText);
    setSummaryJobs((current) => current.map((candidate) => candidate.id === job.id
      ? {
          ...candidate,
          status: "generating",
          jobStage: refreshText ? "detecting" : "summarizing",
          jobProgress: 0,
          jobError: null,
          jobExtractionRequired: refreshText || candidate.jobExtractionRequired,
        }
      : candidate));
  }, []);

  const handlePauseSummaryJob = useCallback(async (job: SummaryJob) => {
    await pauseSummaryJob(job);
    setSummaryJobs((current) => current.map((candidate) => candidate.id === job.id
      ? { ...candidate, status: "paused" }
      : candidate));
  }, []);

  const handleResumeSummaryJob = useCallback(async (job: SummaryJob) => {
    await resumeSummaryJob(job);
    setSummaryJobs((current) => current.map((candidate) => candidate.id === job.id
      ? { ...candidate, status: "generating" }
      : candidate));
  }, []);

  const handleStopSummaryJob = useCallback(async (job: SummaryJob) => {
    await stopSummaryJob(job);
    setSummaryJobs((current) => current.filter((candidate) => candidate.id !== job.id));
  }, []);

  return {
    handleDismissSummaryJob,
    handleRetrySummaryJob,
    handlePauseSummaryJob,
    handleResumeSummaryJob,
    handleStopSummaryJob,
  };
};
