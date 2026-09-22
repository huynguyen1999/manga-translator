import { apiUrl } from './api';

export type SearchMode = 'summary' | 'image' | 'combined';
export type SearchStatusFilter = 'all' | 'summarized' | 'not-summarized';
export interface SearchManga {
  id: string; title: string; pageCount: number; originalCount: number; indexedPages: number;
  outdatedPages: number; summaryAvailable: boolean; summaryStale: boolean;
  summaryIndexed: boolean; summaryOutdated: boolean; outdated: boolean; partial: boolean;
  coverUrl?: string | null;
}
export interface SearchJob {
  id: string; status: string; total: number; completed: number; unchanged: number;
  skipped: number; failed: number; cancelRequested: boolean; error: string | null;
  issues: { sourceKey: string; error: string; groupId: string; title: string; pageNumber: number | null }[];
}
export interface SearchStatus {
  available: boolean; error: string | null; device: string; profile: string;
  models: { summary: string; image: string }; jobs: SearchJob[];
  metrics: { embeddingSeconds: number; embeddedItems: number };
}
export interface SearchResult {
  rank: number; groupId: string; title: string; summarySimilarity: number | null;
  imageSimilarity: number | null; combinedScore: number | null; excerpt: string | null;
  readerUrl: string; coverage: SearchManga;
  pages: { pageId: string; pageNumber: number; similarity: number; imageUrl: string; readerUrl: string }[];
}
export interface SearchResponse {
  results: SearchResult[]; mode: SearchMode; query: string; elapsedMs: number;
  partial: boolean; indexing: boolean; profile: string;
  coverage: { manga: number; pages: number; indexedPages: number; indexedSummaries: number };
}

export const formatSimilarity = (score: number | null) => score === null ? 'Not indexed' : score.toFixed(4);
export const isActiveSearchJob = (job: SearchJob) => job.status === 'queued' || job.status === 'running';
export const finishedSearchItems = (job: SearchJob) => job.completed + job.unchanged + job.skipped + job.failed;

export async function searchRequest<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(apiUrl(`/api/search${path}`), {
    method: body === undefined ? 'GET' : 'POST', signal,
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  const value = await response.json();
  if (!response.ok) {
    const detail = value.detail;
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item: { msg: string }) => item.msg).join('; ') : 'Search request failed. Try again.');
  }
  return value as T;
}
