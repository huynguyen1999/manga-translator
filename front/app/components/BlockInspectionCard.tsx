import React from "react";
import { Icon } from "@iconify/react";
import type { EditableTextBlock } from "@/types";

interface BlockInspectionCardProps {
  block: EditableTextBlock;
  index: number;
  imageCoordinateSize: { width: number; height: number };
  copiedKind: "original" | "translation" | "id" | null;
  onCopy: (text: string, kind: "original" | "translation" | "id") => void;
  onClose: () => void;
}

export const BlockInspectionCard: React.FC<BlockInspectionCardProps> = ({
  block,
  index,
  imageCoordinateSize,
  copiedKind,
  onCopy,
  onClose,
}) => {
  const isCardNearBottom = imageCoordinateSize.height > 0
    ? (block.y + block.height) / imageCoordinateSize.height > 0.62
    : false;
  const clampedCardXPct = imageCoordinateSize.width > 0
    ? Math.max(20, Math.min(80, ((block.x + block.width / 2) / imageCoordinateSize.width) * 100))
    : 50;

  return (
    <div
      className="absolute pointer-events-auto z-40 w-72 sm:w-84 max-w-[90vw] rounded-xl border border-zinc-700 bg-zinc-900/95 p-3.5 text-zinc-100 shadow-2xl backdrop-blur-md animate-in fade-in zoom-in-95 duration-150"
      style={{
        left: `${clampedCardXPct}%`,
        transform: "translateX(-50%)",
        ...(isCardNearBottom
          ? { bottom: `calc(${100 - (block.y / imageCoordinateSize.height) * 100}% + 8px)` }
          : { top: `calc(${((block.y + block.height) / imageCoordinateSize.height) * 100}% + 8px)` }),
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between border-b border-zinc-700/80 pb-2 mb-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-bold text-amber-400">#{index + 1}</span>
          <span className="text-xs font-semibold text-zinc-200 truncate max-w-40">{block.id}</span>
          <button
            type="button"
            onClick={() => void onCopy(block.id, "id")}
            className="text-zinc-400 hover:text-white transition-colors"
            title="Copy region ID"
          >
            <Icon icon={copiedKind === "id" ? "carbon:checkmark" : "carbon:copy"} className="h-3 w-3" />
          </button>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-zinc-400 hover:text-white p-0.5 rounded transition-colors"
          title="Close inspection"
        >
          <Icon icon="carbon:close" className="h-4 w-4" />
        </button>
      </div>

      <div className="space-y-2.5 text-xs">
        {block.original_text && (
          <div>
            <div className="flex items-center justify-between text-[10px] uppercase font-bold tracking-wider text-zinc-400 mb-1">
              <span>Original Japanese</span>
              <button
                type="button"
                onClick={() => void onCopy(block.original_text, "original")}
                className="flex items-center gap-1 text-zinc-400 hover:text-zinc-200 transition-colors lowercase"
              >
                <Icon icon={copiedKind === "original" ? "carbon:checkmark" : "carbon:copy"} className="h-2.5 w-2.5" />
                <span>{copiedKind === "original" ? "Copied" : "Copy"}</span>
              </button>
            </div>
            <div className="rounded bg-black/40 p-2 font-sans text-zinc-200 select-text leading-relaxed border border-white/5">
              {block.original_text}
            </div>
          </div>
        )}

        {block.translation && (
          <div>
            <div className="flex items-center justify-between text-[10px] uppercase font-bold tracking-wider text-indigo-300 mb-1">
              <span>English Translation</span>
              <button
                type="button"
                onClick={() => void onCopy(block.translation, "translation")}
                className="flex items-center gap-1 text-zinc-400 hover:text-zinc-200 transition-colors lowercase"
              >
                <Icon icon={copiedKind === "translation" ? "carbon:checkmark" : "carbon:copy"} className="h-2.5 w-2.5" />
                <span>{copiedKind === "translation" ? "Copied" : "Copy"}</span>
              </button>
            </div>
            <div className="rounded bg-indigo-950/30 p-2 font-sans text-indigo-100 select-text leading-relaxed border border-indigo-500/20">
              {block.translation}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
