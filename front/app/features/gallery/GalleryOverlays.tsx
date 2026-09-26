import React from 'react';
import { Icon } from '@iconify/react';
import { AssignToSeriesModal } from '@/components/AssignToSeriesModal';
import { MangaEditorModal } from '@/components/MangaEditorModal';
import { MangaReaderModal } from '@/components/MangaReaderModal';
import { PageDetailModal } from '@/components/PageDetailModal';
import { GalleryDeleteConfirmationModals } from '@/features/gallery/GalleryDeleteConfirmationModals';
import { MangaSeriesSelectionDock } from '@/features/gallery/MangaSeriesSelectionDock';
import { MangaSummaryModal } from '@/features/gallery/MangaSummaryModal';
import { MoveToMangaModal } from '@/features/gallery/MoveToMangaModal';

type Props = {
  deleteConfirmationProps: React.ComponentProps<typeof GalleryDeleteConfirmationModals>;
  moveToMangaProps: React.ComponentProps<typeof MoveToMangaModal>;
  summaryProps: React.ComponentProps<typeof MangaSummaryModal> | null;
  pageDetailProps: React.ComponentProps<typeof PageDetailModal> | null;
  editorProps: React.ComponentProps<typeof MangaEditorModal> | null;
  readerOpening: boolean;
  readerKey?: string;
  readerProps: React.ComponentProps<typeof MangaReaderModal> | null;
  seriesDockProps: React.ComponentProps<typeof MangaSeriesSelectionDock>;
  assignToSeriesProps: React.ComponentProps<typeof AssignToSeriesModal> | null;
};

export function GalleryOverlays({
  deleteConfirmationProps,
  moveToMangaProps,
  summaryProps,
  pageDetailProps,
  editorProps,
  readerOpening,
  readerKey,
  readerProps,
  seriesDockProps,
  assignToSeriesProps,
}: Props) {
  return (
    <>
      <GalleryDeleteConfirmationModals {...deleteConfirmationProps} />
      <MoveToMangaModal {...moveToMangaProps} />
      {summaryProps && <MangaSummaryModal {...summaryProps} />}
      {pageDetailProps && <PageDetailModal {...pageDetailProps} />}
      {editorProps && <MangaEditorModal {...editorProps} />}
      {readerOpening && (
        <div className="fixed inset-0 z-[100] grid place-items-center bg-zinc-950 text-zinc-100" role="status" aria-label="Opening reader">
          <Icon icon="carbon:renew" className="h-6 w-6 animate-spin" />
        </div>
      )}
      {readerProps && <MangaReaderModal key={readerKey} {...readerProps} />}
      <MangaSeriesSelectionDock {...seriesDockProps} />
      {assignToSeriesProps && <AssignToSeriesModal {...assignToSeriesProps} />}
    </>
  );
}
