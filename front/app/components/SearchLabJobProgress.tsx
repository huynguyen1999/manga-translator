import { useState, useEffect } from 'react';
import { Link } from 'react-router';
import { Icon } from '@iconify/react';
import { apiUrl } from '@/utils/api';
import {
  finishedSearchItems, isActiveSearchJob, type SearchJob,
} from '@/utils/searchLab';

export const fieldClass = 'w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-2 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100';
export const buttonClass = 'rounded-lg border border-zinc-300 px-3 py-2 text-sm font-medium hover:bg-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800';
export const primaryButtonClass = 'rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50';

export function JobProgress({ job, busy, onAction }: { job: SearchJob; busy: boolean; onAction: (action: string, id: string) => void }) {
  const finished = finishedSearchItems(job);
  const active = isActiveSearchJob(job);
  return <section aria-label="Embedding job" className="border-t border-zinc-200 py-4 dark:border-zinc-800">
    <div className="flex items-center justify-between gap-3">
      <h3 className="text-sm font-semibold">Embedding · {job.cancelRequested && active ? 'Cancelling after this batch' : job.status}</h3>
      {active ? <button className={buttonClass} disabled={busy || job.cancelRequested} onClick={() => onAction('cancel', job.id)}>Cancel</button>
        : ['cancelled', 'interrupted', 'error', 'partial'].includes(job.status) ? <button className={buttonClass} disabled={busy} onClick={() => onAction('resume', job.id)}>Resume</button> : null}
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

export function MangaThumbnail({ coverUrl }: { coverUrl?: string | null; title: string }) {
  const [error, setError] = useState(false);
  useEffect(() => { setError(false); }, [coverUrl]);
  if (!coverUrl || error) {
    return <div className="flex h-14 w-10 shrink-0 items-center justify-center rounded-md border border-zinc-200/80 bg-zinc-100 text-zinc-400 dark:border-zinc-800 dark:bg-zinc-800" aria-hidden="true">
      <Icon icon="carbon:book" className="h-4 w-4" />
    </div>;
  }
  return <img src={apiUrl(coverUrl)} alt="" loading="lazy" onError={() => setError(true)} className="h-14 w-10 shrink-0 rounded-md border border-zinc-200/80 bg-zinc-100 object-cover shadow-2xs dark:border-zinc-800 dark:bg-zinc-800" />;
}
