import React from "react";
import { Icon } from "@iconify/react";
import { Link } from "react-router";
import { ImageHandlingArea } from "@/components/ImageHandlingArea";
import { OptionsPanel } from "@/components/OptionsPanel";

interface StudioWorkspaceProps {
  options: React.ComponentProps<typeof OptionsPanel>;
  imageHandling: React.ComponentProps<typeof ImageHandlingArea>;
  translationBatchError: string | null;
  isGroupModalOpen: boolean;
  studioMangaUploadWarning: string | null;
  totalGalleryCount: number;
}

export function StudioWorkspace({
  options,
  imageHandling,
  translationBatchError,
  isGroupModalOpen,
  studioMangaUploadWarning,
  totalGalleryCount,
}: StudioWorkspaceProps) {
  return (
    <div>
      <div className="min-w-0 space-y-6">
        <OptionsPanel {...options} />
        <ImageHandlingArea {...imageHandling} />
        {translationBatchError && !isGroupModalOpen && (
          <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300">
            {translationBatchError} Select the files again to retry.
          </p>
        )}
        {studioMangaUploadWarning && (
          <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
            {studioMangaUploadWarning}
          </p>
        )}

        {/* Link banner to gallery if finished items exist */}
        {totalGalleryCount > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-200 dark:border-indigo-900/60 bg-indigo-50/50 dark:bg-indigo-950/20 p-4 shadow-2xs">
            <div className="flex items-center space-x-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-white shadow-xs">
                <Icon icon="carbon:image" className="h-5 w-5" />
              </div>
              <div>
                <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                  {totalGalleryCount} {totalGalleryCount === 1 ? "page" : "pages"} in gallery
                </div>
                <div className="text-xs text-zinc-500 dark:text-zinc-400">
                  Open full-screen comparison lightbox with zoom and batch download
                </div>
              </div>
            </div>
            <Link
              to="/gallery"
              className="flex items-center space-x-1.5 rounded-lg bg-white dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 px-3.5 py-1.5 text-xs font-semibold text-zinc-800 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors shadow-2xs"
            >
              <span>Open Gallery</span>
              <Icon icon="carbon:arrow-right" className="h-3.5 w-3.5" />
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
