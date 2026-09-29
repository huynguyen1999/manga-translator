export type SearchSummaryFilter = 'all' | 'summarized' | 'not-summarized';
export type SearchIndexFilter = 'all' | 'not-indexed' | 'indexed';
export type SearchStatusFilter = 'all' | 'ready-to-embed' | 'indexed' | 'not-indexed' | 'summarized' | 'not-summarized';

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
  available: boolean; error: string | null; engine?: string; device: string; profile: string;
  models: { summary: string }; jobs: SearchJob[];
  metrics: { embeddingSeconds: number; embeddedItems: number };
}

export interface SearchRetrievalConfig {
  engine: string;
  mode: string;
  candidateLimit: number;
  alpha: number;
  typoTolerance: number;
  hybridRerank: boolean;
}

export interface SearchInitialResult {
  rank: number; groupId: string; title: string; summarySimilarity: number | null;
  vectorDistance?: number | null; textMatch?: number | null; rankFusionScore?: number | null;
  excerpt: string | null; readerUrl: string; coverage: SearchManga;
}

export interface SearchResult extends SearchInitialResult {
  initialRank: number; rankDelta: number;
}

export interface SearchTimings {
  embeddingMs: number;
  retrievalMs: number;
  elapsedMs: number;
}

export interface SearchResponse {
  results: SearchResult[]; initialResults: SearchInitialResult[]; query: string;
  elapsedMs: number; embeddingMs: number; retrievalMs?: number; partial: boolean; indexing: boolean;
  profile: string; coverage: { manga: number; indexedSummaries: number };
  retrieval?: SearchRetrievalConfig;
  timings?: SearchTimings;
}
