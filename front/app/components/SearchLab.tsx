import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { Icon } from '@iconify/react';
import { apiUrl } from '@/utils/api';
import {
  finishedSearchItems, formatSimilarity, isActiveSearchJob, searchRequest,
  type SearchJob, type SearchManga, type SearchMode, type SearchResponse, type SearchStatus,
  type SearchStatusFilter,
} from '@/utils/searchLab';

const field = 'w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-2 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100';
const button = 'rounded-lg border border-zinc-300 px-3 py-2 text-sm font-medium hover:bg-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800';
const primary = 'rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50';
const PAGE_SIZE = 25;

function JobProgress({ job, busy, onAction }: { job: SearchJob; busy: boolean; onAction: (action: string, id: string) => void }) {
  const finished = finishedSearchItems(job);
  const active = isActiveSearchJob(job);
  return <section aria-label="Embedding job" className="border-t border-zinc-200 py-4 dark:border-zinc-800">
    <div className="flex items-center justify-between gap-3">
      <h3 className="text-sm font-semibold">Embedding · {job.cancelRequested && active ? 'Cancelling after this batch' : job.status}</h3>
      {active ? <button className={button} disabled={busy || job.cancelRequested} onClick={() => onAction('cancel', job.id)}>Cancel</button>
        : ['cancelled', 'interrupted', 'error', 'partial'].includes(job.status) ? <button className={button} disabled={busy} onClick={() => onAction('resume', job.id)}>Resume</button> : null}
    </div>
    <progress className="mt-3 h-2 w-full accent-indigo-600" value={finished} max={Math.max(1, job.total)} aria-label="Embedding progress" />
    <p className="mt-2 text-xs leading-5 text-zinc-600 dark:text-zinc-400" aria-live="polite">
      {finished.toLocaleString()} / {job.total.toLocaleString()} items · {job.completed} embedded · {job.unchanged} reused · {job.skipped} skipped · {job.failed} failed
    </p>
    {active && <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">Search is available for completed items. Model files download on first use.</p>}
    {job.error && <p role="alert" className="mt-2 break-words text-sm text-rose-700 dark:text-rose-300">{job.error}</p>}
    {job.issues.length > 0 && <details className="mt-2 text-xs">
      <summary className="cursor-pointer py-1">Skipped or failed items ({job.skipped + job.failed})</summary>
      <ul className="mt-2 max-h-40 space-y-2 overflow-auto text-zinc-600 dark:text-zinc-400">
        {job.issues.map(issue => <li key={issue.sourceKey} className="break-words"><Link to={`/gallery/manga/${encodeURIComponent(issue.groupId)}`} className="font-medium underline underline-offset-2">{issue.title}{issue.pageNumber ? ` · Page ${issue.pageNumber}` : ''}</Link><br />{issue.error}</li>)}
      </ul>
      {job.skipped + job.failed > 20 && <p>Showing the first 20 issues.</p>}
    </details>}
  </section>;
}

function MangaThumbnail({ coverUrl, title }: { coverUrl?: string | null; title: string }) {
  const [error, setError] = useState(false);
  useEffect(() => {
    setError(false);
  }, [coverUrl]);

  if (!coverUrl || error) {
    return (
      <div className="flex h-14 w-10 shrink-0 items-center justify-center rounded-md border border-zinc-200/80 bg-zinc-100 text-zinc-400 dark:border-zinc-800 dark:bg-zinc-800" aria-hidden="true">
        <Icon icon="carbon:book" className="h-4 w-4" />
      </div>
    );
  }
  return (
    <img
      src={apiUrl(coverUrl)}
      alt=""
      loading="lazy"
      onError={() => setError(true)}
      className="h-14 w-10 shrink-0 rounded-md border border-zinc-200/80 bg-zinc-100 object-cover shadow-2xs dark:border-zinc-800 dark:bg-zinc-800"
    />
  );
}

export default function SearchLab() {
  const [status, setStatus] = useState<SearchStatus | null>(null);
  const [serviceError, setServiceError] = useState('');
  const [manga, setManga] = useState<SearchManga[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<SearchStatusFilter>('all');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [loadingManga, setLoadingManga] = useState(true);
  const [libraryError, setLibraryError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<SearchMode>('combined');
  const [scope, setScope] = useState('all');
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const queryAbort = useRef<AbortController | null>(null);
  const activeJob = status?.jobs.find(isActiveSearchJob);
  const latestJob = activeJob || status?.jobs[0];
  const progressKey = latestJob ? `${latestJob.id}:${latestJob.status}:${finishedSearchItems(latestJob)}` : '';

  useEffect(() => {
    const source = new EventSource(apiUrl('/api/search/status/events'));
    source.onmessage = (event) => {
      try {
        setStatus(JSON.parse(event.data) as SearchStatus);
        setServiceError('');
      } catch {
        setServiceError('Could not read Search Lab status. Reconnecting…');
      }
    };
    source.onerror = () => setServiceError('Search Lab status stream disconnected. Reconnecting…');
    return () => source.close();
  }, []);

  const refreshStatus = () => {
    setRefresh(value => value + 1);
    void searchRequest<SearchStatus>('/status')
      .then(value => { setStatus(value); setServiceError(''); })
      .catch(caught => setServiceError((caught as Error).message));
  };

  useEffect(() => {
    const controller = new AbortController();
    setLoadingManga(true);
    const timer = setTimeout(() => {
      const params = new URLSearchParams({
        search: filter,
        status: statusFilter,
        offset: String(offset),
        limit: String(PAGE_SIZE),
      });
      void searchRequest<{ items: SearchManga[]; total: number }>(`/manga?${params.toString()}`, undefined, controller.signal)
        .then(value => { if (!controller.signal.aborted) { setManga(value.items); setTotal(value.total); setLibraryError(''); } })
        .catch(caught => { if (!controller.signal.aborted) setLibraryError(caught.message); })
        .finally(() => { if (!controller.signal.aborted) setLoadingManga(false); });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [filter, statusFilter, offset, refresh, progressKey]);

  useEffect(() => () => queryAbort.current?.abort(), []);
  useEffect(() => {
    queryAbort.current?.abort();
    setSearching(false);
    setResponse(null);
  }, [scope, selected]);

  const act = async (action: string, id?: string) => {
    setBusy(true); setError('');
    try {
      await searchRequest(id ? `/jobs/${encodeURIComponent(id)}/${action}` : '/jobs', id ? {} : { groupIds: [...selected] });
      setRefresh(value => value + 1);
    } catch (caught) { setError((caught as Error).message); }
    finally { setBusy(false); }
  };

  const removeIndex = async (item: SearchManga) => {
    if (!window.confirm(`Remove all Search Lab embeddings for “${item.title}”? The manga and its pages will stay in Gallery.`)) return;
    setBusy(true); setError('');
    try {
      await searchRequest(`/manga/${encodeURIComponent(item.id)}/index`, undefined, undefined, 'DELETE');
      setResponse(null);
      setRefresh(value => value + 1);
    } catch (caught) { setError((caught as Error).message); }
    finally { setBusy(false); }
  };

  const search = async (nextMode = mode) => {
    if (!query.trim()) return;
    queryAbort.current?.abort();
    const controller = new AbortController();
    queryAbort.current = controller;
    setSearching(true); setError(''); setResponse(null);
    try {
      const next = await searchRequest<SearchResponse>('/query', { query, mode: nextMode, groupIds: scope === 'selected' ? [...selected] : null }, controller.signal);
      if (!controller.signal.aborted) setResponse(next);
    } catch (caught) { if (!controller.signal.aborted) setError((caught as Error).message); }
    finally { if (!controller.signal.aborted) setSearching(false); }
  };

  return <div className="space-y-6 text-zinc-900 dark:text-zinc-100">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div><h1 className="text-2xl font-bold tracking-tight">Search Lab</h1>
        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">Find manga by story or scene. Compare summary and original-page matches.</p></div>
      <button className={button} onClick={refreshStatus}>Refresh status</button>
    </header>
    {(serviceError || status?.error) && <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
      <p className="break-words">{serviceError || status?.error}</p><p className="mt-2">Your gallery and translation tools are still available.</p>
    </div>}
    <div className="grid items-start gap-8 lg:grid-cols-[minmax(280px,340px)_minmax(0,1fr)]">
      <aside className="min-w-0 space-y-4 lg:border-r lg:border-zinc-200 lg:pr-6 lg:dark:border-zinc-800" aria-label="Embedding collection">
        <div className="flex items-baseline justify-between gap-2"><h2 className="text-lg font-semibold">Your collection</h2><span className="text-sm text-zinc-600 dark:text-zinc-400">{selected.size} selected</span></div>
        <div className="space-y-3">
          <label className="block text-sm font-medium">Find manga
            <input type="search" className={`${field} mt-1.5`} value={filter} onChange={event => { setFilter(event.target.value); setOffset(0); }} placeholder="Search titles" />
          </label>
          <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400">Filter
            <select
              aria-label="Filter manga collection"
              className={`${field} mt-1`}
              value={statusFilter}
              onChange={event => { setStatusFilter(event.target.value as SearchStatusFilter); setOffset(0); }}
            >
              <option value="all">All manga</option>
              <option value="indexed">Indexed</option>
              <option value="not-indexed">Not indexed</option>
              <option value="summarized">Summarized</option>
              <option value="not-summarized">Not summarized</option>
            </select>
          </label>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className={button} disabled={!manga.length || loadingManga} onClick={() => setSelected(previous => new Set([...previous, ...manga.map(item => item.id)]))}>Select this page</button>
          <button className={button} disabled={!selected.size} onClick={() => setSelected(new Set())}>Clear selection</button>
        </div>
        {libraryError && <p role="alert" className="break-words text-sm text-rose-700 dark:text-rose-300">{libraryError}</p>}
        <div aria-busy={loadingManga} className="max-h-[440px] overflow-y-auto border-y border-zinc-200 dark:border-zinc-800">
          {loadingManga && !manga.length ? <p className="py-6 text-sm" role="status">Loading your collection…</p> : null}
          {!loadingManga && !manga.length && !libraryError ? <p className="py-6 text-sm text-zinc-600 dark:text-zinc-400">{filter ? (statusFilter === 'summarized' ? 'No summarized titles match this filter.' : statusFilter === 'not-summarized' ? 'No unsummarized titles match this filter.' : 'No titles match this filter.') : (statusFilter === 'summarized' ? 'No summarized manga found in your collection.' : statusFilter === 'not-summarized' ? 'All manga in your collection are summarized.' : 'Import manga in Gallery to start your search collection.')}</p> : null}
          {manga.map(item => <div key={item.id} className="border-b border-zinc-100 py-3 last:border-0 dark:border-zinc-800">
            <label className="flex cursor-pointer items-start gap-3">
              <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-indigo-600" checked={selected.has(item.id)} onChange={event => { const checked = event.target.checked; setSelected(previous => { const next = new Set(previous); if (checked) next.add(item.id); else next.delete(item.id); return next; }); }} />
              <MangaThumbnail coverUrl={item.coverUrl} title={item.title} />
              <span className="min-w-0 flex-1"><span className="block break-words text-sm font-medium">{item.title}</span>
                <span className="mt-1 block text-xs leading-5 text-zinc-600 dark:text-zinc-400"><span className="font-medium">{item.summaryIndexed || item.indexedPages ? 'Indexed' : 'Not indexed'}</span> · {item.indexedPages} / {item.pageCount} pages · {item.originalCount} originals<br />Summary: {item.summaryOutdated || item.summaryStale ? 'outdated' : item.summaryIndexed ? 'indexed' : item.summaryAvailable ? 'ready to embed' : 'missing'}</span>
                {item.outdated && <span className="block text-xs text-amber-800 dark:text-amber-300">Refresh embeddings to use current content</span>}
                {(!item.summaryAvailable || item.summaryStale) && <Link className="mt-1.5 inline-block text-xs text-indigo-700 underline underline-offset-2 dark:text-indigo-300" onClick={event => event.stopPropagation()} to={`/gallery/manga/${encodeURIComponent(item.id)}`}>Generate summary in Gallery</Link>}
              </span>
            </label>
            {(item.summaryIndexed || item.indexedPages > 0) && <button className="ml-20 mt-2 rounded-md px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500 disabled:opacity-50 dark:text-rose-300 dark:hover:bg-rose-950/40" aria-label={`Remove indexed data for ${item.title}`} title={activeJob ? 'Wait for embedding to finish' : undefined} disabled={busy || !!activeJob} onClick={() => void removeIndex(item)}>Remove indexed data</button>}
          </div>)}
        </div>
        <div className="flex items-center justify-between gap-2 text-xs">
          <button className={button} disabled={offset === 0 || loadingManga} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous</button>
          <span>{total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total}` : '0 manga'}</span>
          <button className={button} disabled={offset + PAGE_SIZE >= total || loadingManga} onClick={() => setOffset(value => value + PAGE_SIZE)}>Next</button>
        </div>
        <button className={`${primary} w-full`} disabled={!selected.size || busy || !!activeJob || !status?.available} onClick={() => void act('embed')}>{busy ? 'Saving job…' : `Embed selected (${selected.size})`}</button>
        <p className="text-xs leading-5 text-zinc-600 dark:text-zinc-400">Original pages and fresh summaries only. Unchanged embeddings are reused; missing sources are skipped.</p>
        {latestJob && <JobProgress job={latestJob} busy={busy || (!!activeJob && latestJob.id !== activeJob.id)} onAction={(action, id) => void act(action, id)} />}
        {(status?.jobs.length || 0) > 1 && <details><summary className="cursor-pointer text-sm">Earlier jobs</summary>{status?.jobs.filter(job => job.id !== latestJob?.id).map(job => <JobProgress key={job.id} job={job} busy={busy || !!activeJob} onAction={(action, id) => void act(action, id)} />)}</details>}
      </aside>
      <section className="min-w-0 space-y-6" aria-label="Semantic search">
        <form onSubmit={event => { event.preventDefault(); void search(); }} className="space-y-4">
          <label className="block text-sm font-semibold" htmlFor="semantic-query">Describe a story or scene</label>
          <textarea id="semantic-query" className={field} rows={3} value={query} onChange={event => setQuery(event.target.value)} placeholder="A lonely traveler finds an unexpected friend in a ruined city" aria-describedby="query-help" required maxLength={5000} />
          <p id="query-help" className="text-xs text-zinc-600 dark:text-zinc-400">English queries. {mode === 'summary' ? 'Up to 512 model tokens, including the search instruction.' : 'Up to 64 model tokens; keep scene descriptions short.'} Tokens are checked when you search.</p>
          <fieldset className="flex flex-wrap gap-2"><legend className="sr-only">Search mode</legend>
            {(['summary', 'image', 'combined'] as const).map(value => <label key={value} className={`cursor-pointer rounded-lg border px-4 py-2 text-sm has-focus-visible:ring-2 has-focus-visible:ring-indigo-500 ${mode === value ? 'border-indigo-600 bg-indigo-50 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-200' : 'border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800'}`}>
              <input className="sr-only" type="radio" name="search-mode" value={value} checked={mode === value} onChange={() => { setMode(value); if (response || searching) void search(value); }} />{value === 'image' ? 'Images' : value === 'summary' ? 'Summary' : 'Combined'}
            </label>)}
          </fieldset>
          <div className="flex flex-wrap items-end gap-3">
            <label className="min-w-0 flex-1 text-xs font-medium">Search within<select className={`${field} mt-1`} value={scope} onChange={event => { setScope(event.target.value); setResponse(null); }}><option value="all">All indexed manga</option><option value="selected">Selected manga only ({selected.size})</option></select></label>
            <button type="submit" className={primary} disabled={searching || !query.trim() || !status?.available || (scope === 'selected' && !selected.size)}>{searching ? 'Searching…' : 'Search manga'}</button>
          </div>
        </form>
        {error && <p role="alert" className="break-words rounded-lg border border-rose-300 p-3 text-sm text-rose-700 dark:border-rose-900 dark:text-rose-300">{error}</p>}
        <div aria-live="polite" aria-busy={searching}>
          {searching && <p className="py-8 text-sm text-zinc-600 dark:text-zinc-400">Finding matches… The first search may download and load local models.</p>}
          {!response && !searching && <div className="border-t border-zinc-200 py-10 dark:border-zinc-800"><h2 className="text-lg font-semibold">Build a small collection, then compare.</h2><p className="mt-2 max-w-prose text-sm leading-6 text-zinc-600 dark:text-zinc-400">Select a few manga and embed them first. Summary finds story matches; Images finds visual matches in original pages. Combined brings both rankings together.</p></div>}
          {response && <>
            <div className="border-b border-zinc-200 pb-3 dark:border-zinc-800"><h2 className="text-lg font-semibold">{response.results.length} manga found</h2><p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{response.elapsedMs.toLocaleString()} ms · Similarities are cosine scores, not confidence percentages.{response.mode === 'combined' ? ' Combined uses rank fusion, not an average similarity.' : ''}</p>
              <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">Scope coverage: {response.coverage.indexedSummaries} / {response.coverage.manga} summaries · {response.coverage.indexedPages.toLocaleString()} / {response.coverage.pages.toLocaleString()} pages indexed.</p>
              {(response.indexing || response.partial || response.coverage.indexedPages < response.coverage.pages || response.coverage.indexedSummaries < response.coverage.manga) && <p className="mt-2 text-sm text-amber-800 dark:text-amber-300">Partial coverage: results use completed embeddings. Refresh your search as indexing progresses.</p>}
            </div>
            {!response.results.length && <p className="py-8 text-sm text-zinc-600 dark:text-zinc-400">No indexed matches in this scope. Embed manga with an available source, or search all indexed manga.</p>}
            <ol className="divide-y divide-zinc-200 dark:divide-zinc-800">
              {response.results.map(result => <li key={result.groupId} className="py-6">
                <div className="flex items-baseline gap-3"><span className="text-xl font-semibold tabular-nums text-zinc-500 dark:text-zinc-400" aria-label={`Rank ${result.rank}`}>{result.rank}.</span><Link to={result.readerUrl} className="min-w-0 break-words text-lg font-semibold underline-offset-4 hover:underline">{result.title}</Link></div>
                <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-xs">
                  {response.mode !== 'image' && <div><dt className="text-zinc-600 dark:text-zinc-400">Summary similarity</dt><dd className="mt-1 font-semibold tabular-nums">{formatSimilarity(result.summarySimilarity)}</dd></div>}
                  {response.mode !== 'summary' && <div><dt className="text-zinc-600 dark:text-zinc-400">Image similarity</dt><dd className="mt-1 font-semibold tabular-nums">{formatSimilarity(result.imageSimilarity)}</dd></div>}
                  {result.combinedScore !== null && <div><dt className="text-zinc-600 dark:text-zinc-400">Combined ranking score</dt><dd className="mt-1 font-semibold tabular-nums">{result.combinedScore.toFixed(6)}</dd></div>}
                </dl>
                {result.coverage.outdated && <p className="mt-3 text-xs text-amber-800 dark:text-amber-300">Outdated embeddings · showing the indexed summary excerpt. Page previews show the current original.</p>}
                {result.excerpt && <details className="mt-4"><summary className="cursor-pointer text-sm font-medium">Matching summary excerpt</summary><p className="mt-2 max-w-prose whitespace-pre-wrap text-sm leading-6 text-zinc-700 dark:text-zinc-300">{result.excerpt}</p></details>}
                {result.pages.length > 0 && <div className="mt-4 grid grid-cols-3 gap-3">
                  {result.pages.map(page => <Link key={page.pageId} to={page.readerUrl} className="group min-w-0 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">
                    <img src={apiUrl(page.imageUrl)} alt={`${result.title}, original page ${page.pageNumber}`} loading="lazy" className="aspect-[3/4] w-full rounded-lg border border-zinc-200 bg-white object-contain group-hover:border-indigo-500 dark:border-zinc-700" />
                    <span className="mt-2 block text-xs text-zinc-600 dark:text-zinc-400">Page {page.pageNumber} · {formatSimilarity(page.similarity)}</span>
                  </Link>)}
                </div>}
              </li>)}
            </ol>
          </>}
        </div>
        {status && <details className="border-t border-zinc-200 pt-4 text-xs text-zinc-600 dark:border-zinc-800 dark:text-zinc-400"><summary className="cursor-pointer">Local model details</summary><p className="mt-2 break-words leading-6">{status.models.summary}<br />{status.models.image}<br />Device: {status.device}<br />This session: {status.metrics.embeddedItems} items embedded in {status.metrics.embeddingSeconds.toFixed(1)} seconds.</p></details>}
      </section>
    </div>
  </div>;
}
