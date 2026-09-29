import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import {
  formatSimilarity, isActiveSearchJob, searchRequest,
  type SearchIndexFilter, type SearchManga, type SearchResponse,
  type SearchStatus, type SearchSummaryFilter,
} from '@/utils/searchLab';
import {
  buttonClass as button, fieldClass as field, JobProgress,
  MangaThumbnail, primaryButtonClass as primary,
} from '@/components/SearchLabJobProgress';

const PAGE_SIZE = 25;

export default function SearchLab() {
  const [status, setStatus] = useState<SearchStatus | null>(null);
  const [serviceError, setServiceError] = useState('');
  const [manga, setManga] = useState<SearchManga[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState('');
  const [summaryFilter, setSummaryFilter] = useState<SearchSummaryFilter>('all');
  const [indexFilter, setIndexFilter] = useState<SearchIndexFilter>('all');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [loadingManga, setLoadingManga] = useState(true);
  const [selectingAll, setSelectingAll] = useState(false);
  const [libraryError, setLibraryError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [query, setQuery] = useState('');
  const [scope, setScope] = useState('all');
  const [typoTolerance, setTypoTolerance] = useState(2);
  const [alpha, setAlpha] = useState(0.7);
  const [hybridRerank, setHybridRerank] = useState(true);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const queryAbort = useRef<AbortController | null>(null);
  const activeJob = status?.jobs.find(isActiveSearchJob);
  const latestJob = activeJob || status?.jobs[0];

  useEffect(() => {
    const source = new EventSource('/api/search/status/events');
    source.onmessage = (event) => {
      try {
        setStatus(JSON.parse(event.data) as SearchStatus);
        setServiceError('');
      } catch { setServiceError('Could not read Search Lab status. Reconnecting…'); }
    };
    source.onerror = () => setServiceError('Search Lab status stream disconnected. Reconnecting…');
    return () => source.close();
  }, []);

  const refreshStatus = () => {
    setRefresh(v => v + 1);
    void searchRequest<SearchStatus>('/status').then(v => { setStatus(v); setServiceError(''); }).catch(c => setServiceError((c as Error).message));
  };

  useEffect(() => {
    const controller = new AbortController();
    setLoadingManga(true);
    const timer = setTimeout(() => {
      const params = new URLSearchParams({
        search: filter, summary_status: summaryFilter, index_status: indexFilter,
        offset: String(offset), limit: String(PAGE_SIZE),
      });
      void searchRequest<{ items: SearchManga[]; total: number }>(`/manga?${params.toString()}`, undefined, controller.signal)
        .then(v => { if (!controller.signal.aborted) { setManga(v.items); setTotal(v.total); setLibraryError(''); } })
        .catch(c => { if (!controller.signal.aborted) setLibraryError(c.message); })
        .finally(() => { if (!controller.signal.aborted) setLoadingManga(false); });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [filter, summaryFilter, indexFilter, offset, refresh]);

  useEffect(() => () => queryAbort.current?.abort(), []);
  useEffect(() => { queryAbort.current?.abort(); setSearching(false); setResponse(null); }, [scope, selected]);

  const selectAllMatching = async () => {
    if (!total) return;
    setSelectingAll(true);
    try {
      const params = new URLSearchParams({
        search: filter, summary_status: summaryFilter, index_status: indexFilter,
        offset: '0', limit: String(Math.max(PAGE_SIZE, total)),
      });
      const data = await searchRequest<{ items: SearchManga[]; total: number }>(`/manga?${params.toString()}`);
      setSelected(prev => new Set([...prev, ...data.items.map(i => i.id)]));
    } catch (c) { setLibraryError((c as Error).message); }
    finally { setSelectingAll(false); }
  };

  const act = async (action: string, id?: string) => {
    setBusy(true); setError('');
    try {
      await searchRequest(id ? `/jobs/${encodeURIComponent(id)}/${action}` : '/jobs', id ? {} : { groupIds: [...selected] });
      setRefresh(v => v + 1);
    } catch (c) { setError((c as Error).message); } finally { setBusy(false); }
  };

  const removeIndex = async (item?: SearchManga) => {
    if (!window.confirm(item ? `Remove all Search Lab embeddings for “${item.title}”?` : 'Remove all Search Lab embeddings for every manga?')) return;
    setBusy(true); setError('');
    try {
      await searchRequest(item ? `/manga/${encodeURIComponent(item.id)}/index` : '/index', undefined, undefined, 'DELETE');
      setResponse(null);
      if (item) setRefresh(v => v + 1); else refreshStatus();
    } catch (c) { setError((c as Error).message); } finally { setBusy(false); }
  };

  const search = async () => {
    if (!query.trim()) return;
    queryAbort.current?.abort();
    const controller = new AbortController();
    queryAbort.current = controller;
    setSearching(true); setError(''); setResponse(null);
    try {
      const next = await searchRequest<SearchResponse>('/query', {
        query, groupIds: scope === 'selected' ? [...selected] : null,
        typoTolerance, alpha, hybridRerank,
      }, controller.signal);
      if (!controller.signal.aborted) setResponse(next);
    } catch (c) { if (!controller.signal.aborted) setError((c as Error).message); }
    finally { if (!controller.signal.aborted) setSearching(false); }
  };

  return <div className="space-y-6 text-zinc-900 dark:text-zinc-100">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div><h1 className="text-2xl font-bold tracking-tight">Search Lab</h1>
        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">Find manga by story summary with Typesense hybrid retrieval.</p></div>
      <button className={button} onClick={refreshStatus}>Refresh status</button>
    </header>
    {(serviceError || status?.error) && <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
      <p className="break-words">{serviceError || status?.error}</p>
    </div>}
    <div className="grid items-start gap-8 lg:grid-cols-[minmax(280px,340px)_minmax(0,1fr)]">
      <aside className="min-w-0 space-y-4 lg:border-r lg:border-zinc-200 lg:pr-6 lg:dark:border-zinc-800" aria-label="Embedding collection">
        <div className="flex items-baseline justify-between gap-2"><h2 className="text-lg font-semibold">Your collection</h2><span className="text-sm text-zinc-600 dark:text-zinc-400">{selected.size} selected</span></div>
        <div className="space-y-3">
          <label className="block text-sm font-medium">Find manga<input type="search" className={`${field} mt-1.5`} value={filter} onChange={e => { setFilter(e.target.value); setOffset(0); }} placeholder="Search titles" /></label>
          <div className="grid grid-cols-2 gap-2">
            <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400">Summary
              <select aria-label="Filter by summary status" className={`${field} mt-1`} value={summaryFilter} onChange={e => { setSummaryFilter(e.target.value as SearchSummaryFilter); setOffset(0); }}>
                <option value="all">All</option><option value="summarized">Summarized</option><option value="not-summarized">Not summarized</option>
              </select>
            </label>
            <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400">Indexing
              <select aria-label="Filter by indexing status" className={`${field} mt-1`} value={indexFilter} onChange={e => { setIndexFilter(e.target.value as SearchIndexFilter); setOffset(0); }}>
                <option value="all">All</option><option value="not-indexed">Not indexed</option><option value="indexed">Indexed</option>
              </select>
            </label>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className={button} disabled={!total || loadingManga || selectingAll} onClick={() => void selectAllMatching()}>{selectingAll ? 'Selecting…' : `Select all (${total})`}</button>
          <button className={button} disabled={!manga.length || loadingManga || selectingAll} onClick={() => setSelected(prev => new Set([...prev, ...manga.map(i => i.id)]))}>Select this page ({manga.length})</button>
          <button className={button} disabled={!selected.size || selectingAll} onClick={() => setSelected(new Set())}>Clear selection</button>
        </div>
        {libraryError && <p role="alert" className="break-words text-sm text-rose-700 dark:text-rose-300">{libraryError}</p>}
        <div aria-busy={loadingManga} className="max-h-[440px] overflow-y-auto border-y border-zinc-200 dark:border-zinc-800">
          {loadingManga && !manga.length ? <p className="py-6 text-sm" role="status">Loading your collection…</p> : null}
          {!loadingManga && !manga.length && !libraryError ? <p className="py-6 text-sm text-zinc-600 dark:text-zinc-400">No manga match this filter.</p> : null}
          {manga.map(item => <div key={item.id} className="border-b border-zinc-100 py-3 last:border-0 dark:border-zinc-800">
            <label className="flex cursor-pointer items-start gap-3">
              <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-indigo-600" checked={selected.has(item.id)} onChange={e => { const checked = e.target.checked; setSelected(prev => { const n = new Set(prev); if (checked) n.add(item.id); else n.delete(item.id); return n; }); }} />
              <MangaThumbnail coverUrl={item.coverUrl} title={item.title} />
              <span className="min-w-0 flex-1"><span className="block break-words text-sm font-medium">{item.title}</span>
                <span className="mt-1 block text-xs leading-5 text-zinc-600 dark:text-zinc-400"><span className="font-medium">{item.summaryIndexed ? 'Indexed' : 'Not indexed'}</span> · {item.pageCount} pages<br />Summary: {item.summaryOutdated || item.summaryStale ? 'outdated' : item.summaryIndexed ? 'indexed' : item.summaryAvailable ? 'ready to embed' : 'missing'}</span>
                {item.outdated && <span className="block text-xs text-amber-800 dark:text-amber-300">Refresh embeddings to use current summary</span>}
                {(!item.summaryAvailable || item.summaryStale) && <Link className="mt-1.5 inline-block text-xs text-indigo-700 underline underline-offset-2 dark:text-indigo-300" onClick={e => e.stopPropagation()} to={`/gallery/manga/${encodeURIComponent(item.id)}`}>Generate summary in Gallery</Link>}
              </span>
            </label>
            {item.summaryIndexed && <button className="ml-20 mt-2 rounded-md px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50 dark:text-rose-300" disabled={busy || !!activeJob} onClick={() => void removeIndex(item)}>Remove indexed data</button>}
          </div>)}
        </div>
        <div className="flex items-center justify-between gap-2 text-xs">
          <button className={button} disabled={offset === 0 || loadingManga} onClick={() => setOffset(v => Math.max(0, v - PAGE_SIZE))}>Previous</button>
          <span>{total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total}` : '0 manga'}</span>
          <button className={button} disabled={offset + PAGE_SIZE >= total || loadingManga} onClick={() => setOffset(v => v + PAGE_SIZE)}>Next</button>
        </div>
        <button className={`${primary} w-full`} disabled={!selected.size || busy || !!activeJob || !status?.available} onClick={() => void act('embed')}>{busy ? 'Saving job…' : `Embed selected (${selected.size})`}</button>
        <button className={`${button} mt-2 w-full border-rose-300 text-rose-700 hover:bg-rose-50 dark:border-rose-900 dark:text-rose-300`} disabled={busy || !!activeJob || !status?.available} onClick={() => void removeIndex()}>{busy ? 'Working…' : 'Remove all indexed data'}</button>
        {latestJob && <JobProgress job={latestJob} busy={busy || (!!activeJob && latestJob.id !== activeJob.id)} onAction={(action, id) => void act(action, id)} />}
        {(status?.jobs.length || 0) > 1 && <details><summary className="cursor-pointer text-sm">Earlier jobs</summary>{status?.jobs.filter(j => j.id !== latestJob?.id).map(j => <JobProgress key={j.id} job={j} busy={busy || !!activeJob} onAction={(a, id) => void act(a, id)} />)}</details>}
      </aside>
      <section className="min-w-0 space-y-6" aria-label="Semantic search">
        <form onSubmit={e => { e.preventDefault(); void search(); }} className="space-y-4">
          <label className="block text-sm font-semibold" htmlFor="semantic-query">Describe a story or scene</label>
          <textarea id="semantic-query" className={field} rows={3} value={query} onChange={e => setQuery(e.target.value)} placeholder="A lonely traveler finds an unexpected friend in a ruined city" required maxLength={5000} />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="text-xs font-medium">Search within<select className={`${field} mt-1`} value={scope} onChange={e => { setScope(e.target.value); setResponse(null); }}><option value="all">All indexed manga</option><option value="selected">Selected manga only ({selected.size})</option></select></label>
            <label className="text-xs font-medium">Typo tolerance<select aria-label="Typo tolerance" className={`${field} mt-1`} value={typoTolerance} onChange={e => setTypoTolerance(Number(e.target.value))}><option value={2}>2 typos (default)</option><option value={1}>1 typo</option><option value={0}>Off (0 typos)</option></select></label>
          </div>
          <div className="flex justify-end pt-1">
            <button type="submit" className={primary} disabled={searching || !query.trim() || !status?.available || (scope === 'selected' && !selected.size)}>{searching ? 'Searching…' : 'Search manga'}</button>
          </div>
          <details className="rounded-lg border border-zinc-200 bg-zinc-50/50 p-3 text-xs dark:border-zinc-800 dark:bg-zinc-900/30">
            <summary className="cursor-pointer font-medium text-zinc-700 dark:text-zinc-300">Advanced Retrieval Settings</summary>
            <div className="mt-3 space-y-3">
              <label className="block text-xs font-medium">Hybrid Balance: Semantic {Math.round(alpha * 100)}% · Keyword {Math.round((1 - alpha) * 100)}%<input type="range" min="0" max="1" step="0.05" value={alpha} onChange={e => setAlpha(Number(e.target.value))} className="mt-1.5 w-full accent-indigo-600" /></label>
              <label className="flex cursor-pointer items-center gap-2 text-xs"><input type="checkbox" className="h-4 w-4 rounded accent-indigo-600" checked={hybridRerank} onChange={e => setHybridRerank(e.target.checked)} /><span>Enable Typesense hybrid match fusion (rerank_hybrid_matches)</span></label>
            </div>
          </details>
        </form>
        {error && <p role="alert" className="break-words rounded-lg border border-rose-300 p-3 text-sm text-rose-700 dark:border-rose-900 dark:text-rose-300">{error}</p>}
        <div aria-live="polite" aria-busy={searching}>
          {searching && <p className="py-8 text-sm text-zinc-600 dark:text-zinc-400">Finding and ranking matches…</p>}
          {!response && !searching && <div className="border-t border-zinc-200 py-10 dark:border-zinc-800"><h2 className="text-lg font-semibold">Embed summaries, then search with Typesense hybrid retrieval.</h2></div>}
          {response && <div className="space-y-4">
            <div className="border-b border-zinc-200 pb-3 dark:border-zinc-800">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-lg font-semibold">{response.results.length} manga {response.results.length === 1 ? 'match' : 'matches'}</h2>
                <span className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs font-medium text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
                  Typesense hybrid ranking
                </span>
              </div>
              <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{response.elapsedMs.toLocaleString()} ms total (BGE: {response.embeddingMs.toLocaleString()} ms · Typesense: {(response.retrievalMs ?? 0).toLocaleString()} ms)</p>
            </div>
            {response.initialResults.length > 0 && <details className="rounded-lg border border-zinc-200 bg-zinc-50/70 p-3 text-xs dark:border-zinc-800 dark:bg-zinc-900/50">
              <summary className="cursor-pointer font-semibold text-zinc-800 dark:text-zinc-200">Typesense Hybrid Candidates ({response.initialResults.length} candidates)</summary>
              <ol className="mt-3 max-h-72 divide-y divide-zinc-200 overflow-y-auto pr-1 dark:divide-zinc-800">
                {response.initialResults.map(item => <li key={item.groupId} className="py-2">
                  <div className="flex items-baseline justify-between gap-2">
                    <Link to={item.readerUrl} className="font-medium text-zinc-900 underline-offset-2 hover:underline dark:text-zinc-100">#{item.rank}. {item.title}</Link>
                    <div className="flex shrink-0 items-center gap-3 font-mono text-[11px] tabular-nums text-zinc-600 dark:text-zinc-400">
                      {item.textMatch !== null && item.textMatch !== undefined && <span>text: {item.textMatch}</span>}
                      {item.vectorDistance !== null && item.vectorDistance !== undefined && <span>dist: {item.vectorDistance.toFixed(4)}</span>}
                      <span className="font-semibold text-indigo-700 dark:text-indigo-300">score {formatSimilarity(item.summarySimilarity)}</span>
                    </div>
                  </div>
                  {item.excerpt && <p className="mt-1 line-clamp-2 text-zinc-600 dark:text-zinc-400">{item.excerpt}</p>}
                </li>)}
              </ol>
            </details>}
            {!response.results.length && <p className="py-8 text-sm text-zinc-600 dark:text-zinc-400">No matching manga found in this scope.</p>}
            <ol className="divide-y divide-zinc-200 dark:divide-zinc-800">
              {response.results.map(result => <li key={result.groupId} className="py-6">
                <div className="flex flex-wrap items-baseline gap-3">
                  <span className="text-xl font-semibold tabular-nums text-zinc-500 dark:text-zinc-400">{result.rank}.</span>
                  <Link to={result.readerUrl} className="min-w-0 break-words text-lg font-semibold underline-offset-4 hover:underline">{result.title}</Link>
                </div>
                <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-xs">
                  <div><dt className="text-zinc-600 dark:text-zinc-400">Typesense hybrid score</dt><dd className="mt-1 font-semibold tabular-nums">{formatSimilarity(result.summarySimilarity)}</dd></div>
                  {result.textMatch !== null && result.textMatch !== undefined && <div><dt className="text-zinc-600 dark:text-zinc-400">Text match</dt><dd className="mt-1 font-semibold tabular-nums">{result.textMatch}</dd></div>}
                  {result.vectorDistance !== null && result.vectorDistance !== undefined && <div><dt className="text-zinc-600 dark:text-zinc-400">Vector distance</dt><dd className="mt-1 font-semibold tabular-nums">{result.vectorDistance.toFixed(4)}</dd></div>}
                </dl>
                {result.excerpt && <details className="mt-4" open><summary className="cursor-pointer text-sm font-medium">Matching summary excerpt</summary><p className="mt-2 max-w-prose whitespace-pre-wrap text-sm leading-6 text-zinc-700 dark:text-zinc-300">{result.excerpt}</p></details>}
              </li>)}
            </ol>
          </div>}
        </div>
        {status && <details className="border-t border-zinc-200 pt-4 text-xs text-zinc-600 dark:border-zinc-800 dark:text-zinc-400"><summary className="cursor-pointer">Search engine details</summary><p className="mt-2 break-words leading-6">Retrieval engine: {status.engine || 'Typesense'}<br />Embedding: {status.models.summary}<br />Device: {status.device}</p></details>}
      </section>
    </div>
  </div>;
}
