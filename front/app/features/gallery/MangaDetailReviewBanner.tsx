import React from "react";
import { Icon } from "@iconify/react";

interface MangaDetailReviewBannerProps {
  needsReviewCount: number;
  hasImages: boolean;
  mangaTitle: string;
  onReviewNextPage: () => void;
  onAcceptAll: () => void | Promise<void>;
  isAccepting: boolean;
}

export const MangaDetailReviewBanner: React.FC<MangaDetailReviewBannerProps> = ({
  needsReviewCount,
  hasImages,
  mangaTitle,
  onReviewNextPage,
  onAcceptAll,
  isAccepting,
}) => {
  if (needsReviewCount <= 0) return null;

  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-800/70 bg-amber-950/30 px-3.5 py-3 text-amber-100">
      <div className="flex min-w-0 items-start gap-2.5">
        <Icon icon="carbon:warning-alt" className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
        <p className="text-xs leading-5 text-amber-200/85">
          {needsReviewCount} flagged {needsReviewCount === 1 ? "page" : "pages"} remain. Open a page, fix the highlighted bubble, then save to approve it.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2 shrink-0">
        <button
          type="button"
          onClick={onReviewNextPage}
          disabled={!hasImages || isAccepting}
          className="shrink-0 rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-semibold text-amber-950 transition-colors hover:bg-amber-400 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300 cursor-pointer"
          title="Open the next flagged page in the editor"
        >
          Review next page
        </button>
        <button
          type="button"
          onClick={() => void onAcceptAll()}
          disabled={isAccepting}
          className="inline-flex items-center gap-1.5 shrink-0 rounded-lg border border-amber-400/50 bg-amber-900/40 px-3 py-1.5 text-xs font-semibold text-amber-200 transition-colors hover:bg-amber-900/70 hover:text-amber-100 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300 cursor-pointer"
          title="Accept all pages needing review in this manga at once"
          aria-label={`Accept all ${needsReviewCount} flagged pages in ${mangaTitle}`}
        >
          <Icon
            icon={isAccepting ? "carbon:renew" : "carbon:checkmark"}
            className={`w-3.5 h-3.5 ${isAccepting ? "animate-spin" : ""}`}
          />
          <span>{isAccepting ? "Accepting…" : "Accept all"}</span>
        </button>
      </div>
    </div>
  );
};
