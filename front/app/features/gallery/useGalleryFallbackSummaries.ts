import { useEffect, useState } from "react";
import type { MangaGroupSummary } from "@/types";
import { apiUrl } from "@/utils/api";

export async function fetchFallbackGallerySummaries(
  fetcher: typeof fetch = fetch,
): Promise<MangaGroupSummary[] | undefined> {
  const response = await fetcher(apiUrl("/api/results/groups"));
  const data = await response.json();
  return Array.isArray(data.groups) ? data.groups : undefined;
}

export function useGalleryFallbackSummaries(
  mangaSummaries: MangaGroupSummary[] | undefined,
  isLoading: boolean | undefined,
) {
  const [fallbackSummaries, setFallbackSummaries] = useState<MangaGroupSummary[]>([]);
  const [isFallbackLoading, setIsFallbackLoading] = useState(() => (
    isLoading === undefined && (!mangaSummaries || mangaSummaries.length === 0)
  ));

  useEffect(() => {
    if (isLoading === undefined && mangaSummaries === undefined) {
      setIsFallbackLoading(true);
      void fetchFallbackGallerySummaries()
        .then((groups) => {
          if (groups) setFallbackSummaries(groups);
        })
        .catch(() => {})
        .finally(() => setIsFallbackLoading(false));
    }
  }, [mangaSummaries, isLoading]);

  return { fallbackSummaries, isFallbackLoading };
}
