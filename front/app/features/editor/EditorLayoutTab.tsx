import React from "react";
import type { EditableTextBlock } from "@/types";

interface EditorLayoutTabProps {
  selectedBlock: EditableTextBlock;
  updateSelectedBlock: (updates: Partial<EditableTextBlock>) => void;
}

export const EditorLayoutTab: React.FC<EditorLayoutTabProps> = ({
  selectedBlock,
  updateSelectedBlock,
}) => (
  <div className="space-y-4">
    {/* Line Spacing (Leading) */}
    <div>
      <div className="flex justify-between items-center text-xs text-zinc-300 mb-1">
        <span className="font-semibold">Line Spacing (Leading)</span>
        <span className="font-mono text-zinc-400">
          {selectedBlock.line_spacing?.toFixed(2)}x
        </span>
      </div>
      <input
        type="range"
        min="0.8"
        max="2.0"
        step="0.05"
        value={selectedBlock.line_spacing || 1.15}
        onChange={(e) =>
          updateSelectedBlock({
            line_spacing: Number(e.target.value),
          })
        }
        className="w-full accent-indigo-500 cursor-pointer"
      />
    </div>

    {/* Letter Spacing (Tracking) */}
    <div>
      <div className="flex justify-between items-center text-xs text-zinc-300 mb-1">
        <span className="font-semibold">Letter Spacing (Tracking)</span>
        <span className="font-mono text-zinc-400">
          {selectedBlock.letter_spacing || 0} px
        </span>
      </div>
      <input
        type="range"
        min="-2"
        max="8"
        step="0.5"
        value={selectedBlock.letter_spacing || 0}
        onChange={(e) =>
          updateSelectedBlock({
            letter_spacing: Number(e.target.value),
          })
        }
        className="w-full accent-indigo-500 cursor-pointer"
      />
    </div>

    {/* Rotation Angle */}
    <div>
      <div className="flex justify-between items-center text-xs text-zinc-300 mb-1">
        <span className="font-semibold">Rotation Angle</span>
        <span className="font-mono text-zinc-400">
          {selectedBlock.angle || 0}°
        </span>
      </div>
      <input
        type="range"
        min="-45"
        max="45"
        step="1"
        value={selectedBlock.angle || 0}
        onChange={(e) => updateSelectedBlock({ angle: Number(e.target.value) })}
        className="w-full accent-indigo-500 cursor-pointer"
      />
      <div className="flex justify-end mt-1">
        <button
          type="button"
          onClick={() => updateSelectedBlock({ angle: 0 })}
          className="text-[11px] text-zinc-400 hover:text-zinc-200 underline"
        >
          Reset to 0°
        </button>
      </div>
    </div>

    {/* Direction */}
    <div>
      <span className="block text-xs font-semibold text-zinc-300 mb-1.5">
        Text Flow Direction
      </span>
      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => updateSelectedBlock({ direction: "h" })}
          className={`py-1.5 px-3 rounded text-xs border ${
            selectedBlock.direction === "h"
              ? "bg-indigo-600 border-indigo-500 text-white"
              : "bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700"
          }`}
        >
          Horizontal (LTR)
        </button>
        <button
          type="button"
          onClick={() => updateSelectedBlock({ direction: "v" })}
          className={`py-1.5 px-3 rounded text-xs border ${
            selectedBlock.direction === "v"
              ? "bg-indigo-600 border-indigo-500 text-white"
              : "bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700"
          }`}
        >
          Vertical (Manga)
        </button>
      </div>
    </div>

    {/* Position & Size info */}
    <div className="p-3 bg-zinc-950 rounded-lg border border-zinc-800 text-[11px] font-mono space-y-1 text-zinc-400">
      <div className="flex justify-between">
        <span>Position (X, Y):</span>
        <span className="text-zinc-200">
          {selectedBlock.x}, {selectedBlock.y}
        </span>
      </div>
      <div className="flex justify-between">
        <span>Box Size (W x H):</span>
        <span className="text-zinc-200">
          {selectedBlock.width} x {selectedBlock.height}
        </span>
      </div>
    </div>
  </div>
);
