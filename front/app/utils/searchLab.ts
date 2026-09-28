import { apiUrl } from './api';

export type SearchStatusFilter = 'all' | 'indexed' | 'not-indexed' | 'summarized' | 'not-summarized';
export interface SearchManga {
  id: string; title: string; pageCount: number; summaryAvailable: boolean; summaryStale: boolean;
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
  models: { summary: string; reranker: string }; jobs: SearchJob[];
  metrics: { embeddingSeconds: number; embeddedItems: number };
}
export interface SearchInitialResult {
  rank: number; groupId: string; title: string; summarySimilarity: number | null;
  excerpt: string | null; readerUrl: string; coverage: SearchManga;
}
export interface SearchResult extends SearchInitialResult {
  initialRank: number; rankDelta: number; rerankScore: number; rerankLogit: number;
}
export interface SearchResponse {
  results: SearchResult[]; initialResults: SearchInitialResult[]; query: string; minScore: number | null;
  elapsedMs: number; embeddingMs: number; rerankMs: number; partial: boolean; indexing: boolean;
  profile: string; coverage: { manga: number; indexedSummaries: number };
}

export const formatSimilarity = (score: number | null) => score === null ? 'Not indexed' : score.toFixed(4);
export const formatRankDelta = (delta: number, initialRank: number) => delta > 0 ? `↑ +${delta} (from #${initialRank})` : delta < 0 ? `↓ ${delta} (from #${initialRank})` : `= #${initialRank}`;
export const isActiveSearchJob = (job: SearchJob) => job.status === 'queued' || job.status === 'running';
export const finishedSearchItems = (job: SearchJob) => job.completed + job.unchanged + job.skipped + job.failed;

export async function searchRequest<T>(path: string, body?: unknown, signal?: AbortSignal, method?: 'GET' | 'POST' | 'DELETE'): Promise<T> {
  const response = await fetch(apiUrl(`/api/search${path}`), {
    method: method ?? (body === undefined ? 'GET' : 'POST'), signal,
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  const value = await response.json();
  if (!response.ok) {
    const detail = value.detail;
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item: { msg: string }) => item.msg).join('; ') : 'Search request failed. Try again.');
  }
  return value as T;
}
