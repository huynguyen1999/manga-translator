import { Icon } from '@iconify/react';
import type { EditableTextBlock } from '@/types';

interface EditorReviewFooterProps {
  pendingReviewCount: number;
  pendingReviewReason: { label: string; guidance: string };
  selectedBlock: EditableTextBlock | null;
  saving: boolean;
  backgroundError: string | null;
  updateSelectedBlock: (updates: Partial<EditableTextBlock>) => void;
  onSave: () => void | Promise<void>;
}

export function EditorReviewFooter({
  pendingReviewCount,
  pendingReviewReason,
  selectedBlock,
  saving,
  backgroundError,
  updateSelectedBlock,
  onSave,
}: EditorReviewFooterProps) {
  return (
    <footer className="flex shrink-0 flex-col items-stretch gap-2 border-t border-zinc-800 bg-zinc-900/95 px-4 py-3 sm:px-6">
      {pendingReviewCount > 0 && (
        <div role="status" className="min-w-0 flex-1 text-xs text-amber-200">
          <p className="truncate font-semibold">
            {pendingReviewCount} bubble{pendingReviewCount === 1 ? '' : 's'}{' '}
            need review · {pendingReviewReason.label}
          </p>
          <p className="truncate text-amber-100/75">
            {pendingReviewReason.guidance}
          </p>
        </div>
      )}
      <div className="flex w-full min-w-0 flex-wrap items-center justify-start gap-2">
        {selectedBlock?.review_required && (
          <>
            <button
              type="button"
              className="rounded border border-amber-600 px-3 py-2 text-xs font-semibold text-amber-100 hover:bg-amber-900/50"
              onClick={() =>
                updateSelectedBlock({
                  review_required: false,
                  review_reason: null,
                  cover_background: true,
                })
              }
            >
              Replace original
            </button>
            <button
              type="button"
              className="rounded border border-zinc-600 px-3 py-2 text-xs text-zinc-200 hover:bg-zinc-800"
              onClick={() =>
                updateSelectedBlock({
                  translation: '',
                  review_required: false,
                  review_reason: null,
                  cover_background: false,
                })
              }
            >
              Keep original
            </button>
          </>
        )}
        {pendingReviewCount > 0 && (
          <button
            type="button"
            onClick={onSave}
            disabled={saving || Boolean(backgroundError)}
            className="flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-3.5 py-2 text-xs font-semibold text-zinc-200 transition-all hover:bg-zinc-700 disabled:bg-zinc-900 disabled:text-zinc-500"
            title="Save the current edits without approving the page"
          >
            <Icon
              icon={saving ? 'carbon:circle-dash' : 'carbon:save'}
              className={`h-4 w-4 ${saving ? 'animate-spin' : ''}`}
            />
            <span>{saving ? 'Saving...' : 'Save draft'}</span>
          </button>
        )}
        <button
          type="button"
          onClick={onSave}
          disabled={
            saving || Boolean(backgroundError) || pendingReviewCount > 0
          }
          className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white shadow-md transition-all hover:bg-indigo-500 disabled:bg-indigo-900 disabled:text-indigo-300"
          title={
            pendingReviewCount > 0
              ? `Resolve ${pendingReviewCount} flagged bubble${pendingReviewCount === 1 ? '' : 's'} before approving`
              : 'Save edits and approve this page'
          }
        >
          <Icon
            icon={saving ? 'carbon:circle-dash' : 'carbon:save'}
            className={`h-4 w-4 ${saving ? 'animate-spin' : ''}`}
          />
          <span>{saving ? 'Saving...' : 'Save & approve page'}</span>
        </button>
      </div>
    </footer>
  );
}
