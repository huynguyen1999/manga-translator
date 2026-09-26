import { useEffect, useState } from "react";
import { apiUrl } from "@/utils/api";
import { shouldLoadTranslationArtifacts } from "@/utils/pageDetailTiming";
import type { FinishedImage, PipelineRunManifest } from "@/types";
import type { ProfessionalTranslationAudit } from "./ProfessionalAudit";

export async function loadDiscoveredBubbleMaskUrl(
  isOriginal: boolean,
  bubbleMaskUrl: string | null,
  folder: string | null,
): Promise<string | null> {
  if (isOriginal || bubbleMaskUrl || !folder) return null;
  const maskUrl = apiUrl(`/result/${encodeURIComponent(folder)}/bubble_mask.png`);
  try {
    const response = await fetch(maskUrl, { method: "HEAD", cache: "no-store" });
    return response.ok ? maskUrl : null;
  } catch {
    return null;
  }
}

export async function loadPipelineManifest(folder: string): Promise<PipelineRunManifest | null> {
  const response = await fetch(apiUrl(`/pipeline-runs/${encodeURIComponent(folder)}/manifest`), {
    cache: "no-store",
  });
  if (response.ok) return response.json();
  const fallback = await fetch(apiUrl(`/result/${encodeURIComponent(folder)}/pipeline_manifest.json`), {
    cache: "no-store",
  });
  return fallback.ok ? fallback.json() : null;
}

export async function loadTranslationArtifacts(
  folder: string,
  sourceType: FinishedImage["sourceType"],
): Promise<[Record<string, unknown> | null, ProfessionalTranslationAudit | null]> {
  if (!shouldLoadTranslationArtifacts(sourceType)) return [null, null];
  return Promise.all([
    fetch(apiUrl(`/result/${encodeURIComponent(folder)}/translation_detail.json`), {
      cache: "no-store",
    }).then((response) => response.ok ? response.json() : null),
    fetch(apiUrl(`/result/${encodeURIComponent(folder)}/professional_translation.json`), {
      cache: "no-store",
    }).then((response) => response.ok ? response.json() : null),
  ]);
}

export function usePageDetailArtifacts(
  folder: string | null,
  sourceType: FinishedImage["sourceType"],
  isOriginal: boolean,
  bubbleMaskUrl: string | null,
) {
  const [discoveredBubbleMaskUrl, setDiscoveredBubbleMaskUrl] = useState<string | null>(null);
  const [pipelineManifest, setPipelineManifest] = useState<PipelineRunManifest | null>(null);
  const [isPipelineTimingLoading, setIsPipelineTimingLoading] = useState(false);
  const [translationDetail, setTranslationDetail] = useState<Record<string, unknown> | null>(null);
  const [professionalAudit, setProfessionalAudit] = useState<ProfessionalTranslationAudit | null>(null);
  const [isTranslationDetailLoading, setIsTranslationDetailLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setDiscoveredBubbleMaskUrl(null);
    void loadDiscoveredBubbleMaskUrl(isOriginal, bubbleMaskUrl, folder).then((url) => {
      if (!cancelled && url) setDiscoveredBubbleMaskUrl(url);
    });
    return () => {
      cancelled = true;
    };
  }, [bubbleMaskUrl, folder, isOriginal]);

  useEffect(() => {
    let cancelled = false;
    setPipelineManifest(null);
    setIsPipelineTimingLoading(false);
    if (!folder) return;

    setIsPipelineTimingLoading(true);
    loadPipelineManifest(folder)
      .then((manifest) => {
        if (!cancelled && manifest) setPipelineManifest(manifest);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setIsPipelineTimingLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [folder]);

  useEffect(() => {
    let cancelled = false;
    setTranslationDetail(null);
    setProfessionalAudit(null);
    setIsTranslationDetailLoading(false);
    if (!folder || !shouldLoadTranslationArtifacts(sourceType)) return;

    setIsTranslationDetailLoading(true);
    loadTranslationArtifacts(folder, sourceType)
      .then(([detail, audit]) => {
        if (cancelled) return;
        if (detail) setTranslationDetail(detail);
        if (audit?.regions?.length || audit?.analysis?.stories?.length) setProfessionalAudit(audit);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setIsTranslationDetailLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [folder, sourceType]);

  return {
    discoveredBubbleMaskUrl,
    pipelineManifest,
    isPipelineTimingLoading,
    translationDetail,
    professionalAudit,
    isTranslationDetailLoading,
  };
}
