import type { FinishedImage, SeriesDetail, SeriesMember, SeriesSummary } from "@/types";
import { apiUrl } from "./api";

export interface SeriesPage {
  series: SeriesSummary[];
  totalSeries: number;
  nextOffset: number | null;
}

export const reorderSeriesMembers = <T extends { id: string }>(
  members: readonly T[],
  sourceId: string,
  targetId: string,
): T[] => {
  const sourceIndex = members.findIndex((member) => member.id === sourceId);
  const targetIndex = members.findIndex((member) => member.id === targetId);
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return [...members];
  const next = [...members];
  const [moved] = next.splice(sourceIndex, 1);
  next.splice(targetIndex - Number(sourceIndex < targetIndex), 0, moved);
  return next;
};

const mapCover = (cover: any): FinishedImage | null => {
  if (!cover || typeof cover !== "object") return null;
  return {
    id: cover.id || cover.folder,
    groupId: cover.groupId || null,
    originalName: cover.originalName || cover.id || "cover",
    pageOrder: cover.pageOrder ?? null,
    sourcePath: cover.sourcePath ?? null,
    result: cover.resultUrl ? apiUrl(cover.resultUrl) : "",
    thumbnailUrl: cover.thumbnailUrl ? apiUrl(cover.thumbnailUrl) : null,
    coverUrl: cover.coverUrl ? apiUrl(cover.coverUrl) : null,
    batchPreviewUrl: cover.batchPreviewUrl ? apiUrl(cover.batchPreviewUrl) : null,
    detailPreviewUrl: cover.detailPreviewUrl ? apiUrl(cover.detailPreviewUrl) : null,
    readerUrl: cover.readerUrl ? apiUrl(cover.readerUrl) : null,
    fullUrl: cover.fullUrl ? apiUrl(cover.fullUrl) : null,
    inputUrl: cover.inputUrl
      ? apiUrl(cover.inputUrl)
      : (cover.folder ? apiUrl(`/result/${cover.folder}/input.png`) : null),
    sourceType: cover.sourceType === "original" ? "original" : "translated",
    folder: cover.folder,
    mangaTitle: cover.mangaTitle,
    seriesId: cover.seriesId,
    seriesTitle: cover.seriesTitle,
    finishedAt: cover.finishedAt || new Date().toISOString(),
    settings: cover.settings || {},
  };
};

const mapMember = (member: any): SeriesMember => ({
  id: String(member.id),
  title: String(member.title || "Untitled"),
  position: Number(member.position),
  count: Number(member.count || 0),
  latestFinishedAt: member.latestFinishedAt,
  cover: mapCover(member.cover),
});

const mapSummary = (series: any): SeriesSummary => ({
  id: String(series.id),
  title: String(series.title || "Untitled"),
  memberCount: Number(series.memberCount || 0),
  cover: mapCover(series.cover),
  firstGroupId: series.firstGroupId || (series.cover?.groupId ? String(series.cover.groupId) : null),
  updatedAt: series.updatedAt,
});

const checkResponse = async (response: Response, action: string) => {
  if (response.ok) return;
  const detail = await response.text();
  throw new Error(detail || `${action} failed (${response.status})`);
};

export const fetchSeries = async (
  limit = 24,
  offset = 0,
  search = "",
): Promise<SeriesPage> => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (search.trim()) params.set("search", search.trim());
  const response = await fetch(apiUrl(`/api/series?${params.toString()}`));
  await checkResponse(response, "Loading series");
  const data = await response.json();
  return {
    series: Array.isArray(data.series) ? data.series.map(mapSummary) : [],
    totalSeries: Number(data.totalSeries || 0),
    nextOffset: data.nextOffset ?? null,
  };
};

export const fetchAllSeries = async (search = ""): Promise<SeriesSummary[]> => {
  const list: SeriesSummary[] = [];
  let offset = 0;
  while (true) {
    const page = await fetchSeries(500, offset, search);
    list.push(...page.series);
    if (page.nextOffset === null || page.nextOffset === undefined || list.length >= page.totalSeries) {
      break;
    }
    offset = page.nextOffset;
  }
  return list;
};

export const fetchSeriesDetail = async (seriesId: string): Promise<SeriesDetail> => {
  const response = await fetch(apiUrl(`/api/series/${encodeURIComponent(seriesId)}`));
  await checkResponse(response, "Loading series");
  const data = await response.json();
  return {
    ...mapSummary(data),
    members: Array.isArray(data.members) ? data.members.map(mapMember) : [],
  };
};

export const fetchGroupSeries = async (groupId: string): Promise<SeriesDetail | null> => {
  const response = await fetch(apiUrl(`/api/manga/${encodeURIComponent(groupId)}/series`));
  await checkResponse(response, "Loading series navigation");
  const data = await response.json();
  if (!data.series) return null;
  return {
    ...mapSummary(data.series),
    members: Array.isArray(data.series.members) ? data.series.members.map(mapMember) : [],
  };
};

export const createSeries = async (title: string, groupIds: string[]): Promise<SeriesDetail> => {
  const response = await fetch(apiUrl("/api/series"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, groupIds }),
  });
  await checkResponse(response, "Creating series");
  const data = await response.json();
  return {
    ...mapSummary(data),
    members: Array.isArray(data.members) ? data.members.map(mapMember) : [],
  };
};

export const renameSeries = async (seriesId: string, title: string): Promise<SeriesDetail> => {
  const response = await fetch(apiUrl(`/api/series/${encodeURIComponent(seriesId)}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  await checkResponse(response, "Renaming series");
  const data = await response.json();
  return {
    ...mapSummary(data),
    members: Array.isArray(data.members) ? data.members.map(mapMember) : [],
  };
};

export const replaceSeriesMembers = async (
  seriesId: string,
  groupIds: string[],
): Promise<SeriesDetail> => {
  const response = await fetch(apiUrl(`/api/series/${encodeURIComponent(seriesId)}/members`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ groupIds }),
  });
  await checkResponse(response, "Updating series members");
  const data = await response.json();
  return {
    ...mapSummary(data),
    members: Array.isArray(data.members) ? data.members.map(mapMember) : [],
  };
};

export const addMangaToSeries = async (
  seriesId: string,
  groupIds: string[],
): Promise<SeriesDetail> => {
  const response = await fetch(apiUrl(`/api/series/${encodeURIComponent(seriesId)}/members/add`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ groupIds }),
  });
  await checkResponse(response, "Adding manga to series");
  const data = await response.json();
  return {
    ...mapSummary(data),
    members: Array.isArray(data.members) ? data.members.map(mapMember) : [],
  };
};

export const moveMangaToSeries = async (
  mangaId: string,
  targetSeriesId: string,
): Promise<SeriesDetail> => {
  const response = await fetch(apiUrl(`/api/manga/${encodeURIComponent(mangaId)}/series/move`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ targetSeriesId }),
  });
  await checkResponse(response, "Moving manga to series");
  const data = await response.json();
  return {
    ...mapSummary(data),
    members: Array.isArray(data.members) ? data.members.map(mapMember) : [],
  };
};

export const removeMangaFromSeries = async (mangaId: string): Promise<void> => {
  const response = await fetch(apiUrl(`/api/manga/${encodeURIComponent(mangaId)}/series`), {
    method: "DELETE",
  });
  await checkResponse(response, "Removing manga from series");
};

export const deleteSeries = async (seriesId: string): Promise<void> => {
  const response = await fetch(apiUrl(`/api/series/${encodeURIComponent(seriesId)}`), {
    method: "DELETE",
  });
  await checkResponse(response, "Deleting series");
};
