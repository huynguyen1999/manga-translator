import { apiUrl } from "@/utils/api";
import { mangaIdForTitle } from "@/utils/routeState";

export async function approveMangaReview(
  groupId: string,
  mangaTitle: string,
  fetcher: typeof fetch = fetch,
): Promise<{
  approvedCount: number;
  mangaTitle?: string;
  groupId?: string;
  status?: string;
}> {
  const targetId = groupId || mangaIdForTitle(mangaTitle);
  const response = await fetcher(
    apiUrl(`/api/manga/${encodeURIComponent(targetId)}/review/approve-all`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }
  );
  if (!response.ok) {
    const errorPayload = (await response.json().catch(() => ({}))) as {
      detail?: string;
    };
    throw new Error(
      errorPayload.detail ||
        `Failed to accept manga review pages (${response.status})`
    );
  }
  return (await response.json().catch(() => ({}))) as {
    approvedCount: number;
    mangaTitle?: string;
    groupId?: string;
    status?: string;
  };
}
