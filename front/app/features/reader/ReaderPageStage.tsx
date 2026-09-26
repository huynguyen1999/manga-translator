import type { MutableRefObject, RefObject } from 'react';
import { Icon } from '@iconify/react';
import type { FinishedImage } from '@/types';
import { PageItem, getReaderTitle } from '@/features/reader/PageItem';
import type { ReaderMode, ReaderWidth, SinglePageFit } from '@/features/reader/PageItem';

interface ReaderPageStageProps {
  images: FinishedImage[];
  mangaTitle: string;
  readerMode: ReaderMode;
  touchDevice: boolean;
  currentPage: number;
  readerWidth: ReaderWidth;
  singlePageFit: SinglePageFit;
  showControls: boolean;
  priorityPageIndices: ReadonlySet<number>;
  pageRefs: MutableRefObject<(HTMLDivElement | null)[]>;
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  singlePageScrollRef: RefObject<HTMLDivElement | null>;
  onEditImage?: (image: FinishedImage) => void;
  onScroll: () => void;
  onToggleControls: () => void;
  onJumpToPage: (page: number) => void;
  onPreviousPage: () => void;
  onNextPage: () => void;
}

export function ReaderPageStage({
  images,
  mangaTitle,
  readerMode,
  touchDevice,
  currentPage,
  readerWidth,
  singlePageFit,
  showControls,
  priorityPageIndices,
  pageRefs,
  scrollContainerRef,
  singlePageScrollRef,
  onEditImage,
  onScroll,
  onToggleControls,
  onJumpToPage,
  onPreviousPage,
  onNextPage,
}: ReaderPageStageProps) {
  return (
    <main className={`${touchDevice ? 'relative flex-none overflow-visible' : 'relative flex-1 h-full overflow-hidden'} w-full bg-zinc-950 flex flex-col`} aria-label="Manga pages">
      {readerMode === 'infinite' ? (
        /* ── Infinite Scroll Mode ── */
        <div
          ref={scrollContainerRef}
          onScroll={onScroll}
          onClick={(event) => {
            const target = event.target as HTMLElement;
            if (target.closest('button, a, input, select, textarea')) return;
            onToggleControls();
          }}
          className={`${touchDevice ? 'w-full overflow-visible' : 'flex-1 h-full overflow-y-auto overflow-x-hidden manga-reader-scroll'} cursor-pointer`}
          style={{ overscrollBehavior: touchDevice ? 'auto' : 'contain' }}
        >
          <div className="flex flex-col items-center w-full">
            {images.map((image, index) => (
              <div
                key={image.id}
                ref={(element) => { pageRefs.current[index] = element; }}
                data-page-index={index}
                className="flex w-full justify-center bg-zinc-950"
              >
                <PageItem
                  image={image}
                  index={index}
                  readerWidth={readerWidth}
                  onEditImage={onEditImage}
                  isPriority={priorityPageIndices.has(index)}
                  showControls={showControls}
                />
              </div>
            ))}

            {/* End of Manga Banner */}
            <div className="py-12 flex flex-col items-center space-y-2 text-zinc-400">
              <Icon icon="carbon:checkmark-filled" className="w-8 h-8 text-indigo-500" />
              <p className="text-sm font-semibold text-zinc-300">End of {mangaTitle}</p>
              <p className="text-xs text-zinc-400">{images.length} pages read</p>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onJumpToPage(1);
                }}
                className="mt-2 flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-300 transition-colors"
              >
                <Icon icon="carbon:arrow-up" className="w-3.5 h-3.5" />
                <span>Back to Top</span>
              </button>
            </div>
          </div>
        </div>
      ) : (
        /* ── Single Page Mode ── */
        <div
          ref={singlePageScrollRef}
          onScroll={onScroll}
          onClick={(event) => {
            const target = event.target as HTMLElement;
            if (target.closest('button, a, input, select, textarea')) return;
            onToggleControls();
          }}
          className={`${touchDevice ? 'relative flex-none w-full overflow-visible' : 'relative flex-1 h-full overflow-y-auto overflow-x-hidden manga-reader-scroll'} flex flex-col items-center justify-center p-0 sm:p-2 cursor-pointer`}
        >
          {/* Click Navigation Overlay Zones */}
          <div
            onClick={(event) => {
              event.stopPropagation();
              onPreviousPage();
            }}
            className={`absolute top-0 bottom-0 left-0 w-1/4 z-10 cursor-w-resize group/left flex items-center justify-start pl-4 transition-opacity ${
              currentPage === 1 ? 'pointer-events-none' : ''
            }`}
            title={getReaderTitle('Previous Page (ArrowLeft / A)', touchDevice)}
          >
            <div className="p-3 rounded-full bg-black/50 text-white/40 group-hover/left:text-white group-hover/left:bg-black/70 backdrop-blur-sm opacity-0 group-hover/left:opacity-100 transition-all">
              <Icon icon="carbon:chevron-left" className="w-6 h-6" />
            </div>
          </div>

          <div
            onClick={(event) => {
              event.stopPropagation();
              onNextPage();
            }}
            className={`absolute top-0 bottom-0 right-0 w-1/4 z-10 cursor-e-resize group/right flex items-center justify-end pr-4 transition-opacity ${
              currentPage === images.length ? 'pointer-events-none' : ''
            }`}
            title={getReaderTitle('Next Page (ArrowRight / Space / D)', touchDevice)}
          >
            <div className="p-3 rounded-full bg-black/50 text-white/40 group-hover/right:text-white group-hover/right:bg-black/70 backdrop-blur-sm opacity-0 group-hover/right:opacity-100 transition-all">
              <Icon icon="carbon:chevron-right" className="w-6 h-6" />
            </div>
          </div>

          {/* Active Single Page */}
          {images[currentPage - 1] && (
            <PageItem
              key={images[currentPage - 1].id}
              image={images[currentPage - 1]}
              index={currentPage - 1}
              readerWidth={readerWidth}
              onEditImage={onEditImage}
              isSinglePage={true}
              singlePageFit={singlePageFit}
              isPriority={true}
              showControls={showControls}
            />
          )}
        </div>
      )}
    </main>
  );
}
