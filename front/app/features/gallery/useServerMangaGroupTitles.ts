import { useCallback, useRef, useState } from "react";
import { apiUrl } from "@/utils/api";

export const useServerMangaGroupTitles = () => {
  const [serverGroupTitles, setServerGroupTitles] = useState<string[]>([]);
  const [isLoadingServerGroupTitles, setIsLoadingServerGroupTitles] = useState(false);
  const serverGroupTitlesRequestRef = useRef<Promise<string[]> | null>(null);

  const loadAllServerGroupTitles = useCallback(async (): Promise<string[]> => {
    const pending = serverGroupTitlesRequestRef.current;
    if (pending) return pending;

    if (serverGroupTitles.length === 0) {
      setIsLoadingServerGroupTitles(true);
    }
    const request = (async () => {
      const titles = new Set<string>();
      let offset = 0;
      while (true) {
        const response = await fetch(apiUrl(`/api/results/groups?limit=500&offset=${offset}`));
        if (!response.ok) throw new Error(`Could not load manga groups (${response.status})`);
        const data = await response.json();
        if (!Array.isArray(data.groups)) break;
        data.groups.forEach((group: { title?: unknown }) => {
          if (typeof group.title === "string" && group.title.trim() && group.title.trim() !== "Ungrouped") {
            titles.add(group.title.trim());
          }
        });
        if (data.nextOffset === null || data.nextOffset === undefined) break;
        const nextOffset = Number(data.nextOffset);
        if (!Number.isFinite(nextOffset) || nextOffset <= offset) break;
        offset = nextOffset;
      }
      const result = Array.from(titles);
      setServerGroupTitles(result);
      return result;
    })().catch((error) => {
      console.warn("Failed to load all manga group titles:", error);
      return [];
    });
    serverGroupTitlesRequestRef.current = request;
    void request.then(() => {
      setIsLoadingServerGroupTitles(false);
      if (serverGroupTitlesRequestRef.current === request) {
        serverGroupTitlesRequestRef.current = null;
      }
    });
    return request;
  }, [serverGroupTitles.length]);

  return { serverGroupTitles, isLoadingServerGroupTitles, loadAllServerGroupTitles };
};
