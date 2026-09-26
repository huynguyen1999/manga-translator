import { Icon } from '@iconify/react';

interface GalleryDeleteConfirmationModalsProps {
  confirmDeleteManga: string | null;
  deleteMangaPageCount: number | string;
  onCancelMangaDeletion: () => void;
  onConfirmMangaDeletion: () => void;
  confirmDeleteSelectedPages: boolean;
  selectedPageIds: ReadonlySet<string>;
  isDeletingSelectedPages: boolean;
  onCancelPageDeletion: () => void;
  onConfirmPageDeletion: () => void;
  confirmDeleteSelectedMangas: boolean;
  selectedMangaIds: ReadonlyMap<string, string>;
  isDeletingSelectedMangas: boolean;
  onCancelMangaGroupDeletion: () => void;
  onConfirmMangaGroupDeletion: () => void;
}

export function GalleryDeleteConfirmationModals({
  confirmDeleteManga,
  deleteMangaPageCount,
  onCancelMangaDeletion,
  onConfirmMangaDeletion,
  confirmDeleteSelectedPages,
  selectedPageIds,
  isDeletingSelectedPages,
  onCancelPageDeletion,
  onConfirmPageDeletion,
  confirmDeleteSelectedMangas,
  selectedMangaIds,
  isDeletingSelectedMangas,
  onCancelMangaGroupDeletion,
  onConfirmMangaGroupDeletion,
}: GalleryDeleteConfirmationModalsProps) {
  return (
    <>
      {confirmDeleteManga && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4"
        >
          <div
            className="w-full max-w-sm rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 shadow-2xl space-y-4"
          >
            <div className="flex items-center space-x-3 text-red-600 dark:text-red-400">
              <Icon icon="carbon:warning" className="w-6 h-6 shrink-0" />
              <h3 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                Delete Manga
              </h3>
            </div>
            <p className="text-xs text-zinc-600 dark:text-zinc-400">
              Are you sure you want to delete &ldquo;<strong className="text-zinc-900 dark:text-zinc-200">{confirmDeleteManga}</strong>&rdquo;? All {
                deleteMangaPageCount
              } pages will be removed from your gallery.
            </p>
            <div className="flex items-center justify-end space-x-2 pt-2">
              <button
                type="button"
                onClick={onCancelMangaDeletion}
                className="rounded-lg px-3 py-1.5 text-xs text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={onConfirmMangaDeletion}
                className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-500 transition-colors cursor-pointer"
              >
                Delete Manga
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmDeleteSelectedPages && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-delete-selected-pages-title"
        >
          <div
            className="w-full max-w-sm rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 shadow-2xl space-y-4"
          >
            <div className="flex items-center space-x-3 text-red-600 dark:text-red-400">
              <Icon icon="carbon:warning" className="w-6 h-6 shrink-0" />
              <h3 id="confirm-delete-selected-pages-title" className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                Delete Selected Pages
              </h3>
            </div>
            <p className="text-xs text-zinc-600 dark:text-zinc-400">
              Are you sure you want to delete <strong className="text-zinc-900 dark:text-zinc-200">{selectedPageIds.size}</strong> selected {selectedPageIds.size === 1 ? 'page' : 'pages'}? This action cannot be undone.
            </p>
            <div className="flex items-center justify-end space-x-2 pt-2">
              <button
                type="button"
                disabled={isDeletingSelectedPages}
                onClick={onCancelPageDeletion}
                className="rounded-lg px-3 py-1.5 text-xs text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isDeletingSelectedPages}
                onClick={onConfirmPageDeletion}
                className="flex items-center space-x-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-500 disabled:opacity-50 transition-colors cursor-pointer"
              >
                {isDeletingSelectedPages ? (
                  <Icon icon="carbon:renew" className="w-4 h-4 animate-spin" />
                ) : (
                  <Icon icon="carbon:trash-can" className="w-4 h-4" />
                )}
                <span>Delete {selectedPageIds.size} {selectedPageIds.size === 1 ? 'Page' : 'Pages'}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmDeleteSelectedMangas && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-delete-selected-mangas-title"
        >
          <div
            className="w-full max-w-md rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 shadow-2xl space-y-4"
          >
            <div className="flex items-center space-x-3 text-red-600 dark:text-red-400">
              <Icon icon="carbon:warning" className="w-6 h-6 shrink-0" />
              <h3 id="confirm-delete-selected-mangas-title" className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                Delete Selected Manga
              </h3>
            </div>
            <div className="space-y-2">
              <p className="text-xs text-zinc-600 dark:text-zinc-400">
                Are you sure you want to delete <strong className="text-zinc-900 dark:text-zinc-200">{selectedMangaIds.size}</strong> selected {selectedMangaIds.size === 1 ? 'manga' : 'manga groups'}? All pages inside will be permanently removed.
              </p>
              <div className="max-h-36 overflow-y-auto rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-800/50 p-2 space-y-1">
                {Array.from(selectedMangaIds.entries()).map(([id, title]) => (
                  <div key={id} className="text-xs text-zinc-700 dark:text-zinc-300 font-medium truncate flex items-center gap-1.5">
                    <Icon icon="carbon:book" className="w-3.5 h-3.5 text-zinc-400 shrink-0" />
                    <span className="truncate">{title}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="flex items-center justify-end space-x-2 pt-2">
              <button
                type="button"
                disabled={isDeletingSelectedMangas}
                onClick={onCancelMangaGroupDeletion}
                className="rounded-lg px-3 py-1.5 text-xs text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isDeletingSelectedMangas}
                onClick={onConfirmMangaGroupDeletion}
                className="flex items-center space-x-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-500 disabled:opacity-50 transition-colors cursor-pointer"
              >
                {isDeletingSelectedMangas ? (
                  <Icon icon="carbon:renew" className="w-4 h-4 animate-spin" />
                ) : (
                  <Icon icon="carbon:trash-can" className="w-4 h-4" />
                )}
                <span>Delete {selectedMangaIds.size} Manga</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
