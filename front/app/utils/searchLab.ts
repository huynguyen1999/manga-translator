import { apiUrl } from './api';

export * from './searchLabTypes';
import type { SearchJob } from './searchLabTypes';

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
