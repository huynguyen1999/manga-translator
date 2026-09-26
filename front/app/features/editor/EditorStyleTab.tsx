import React from "react";
import { Icon } from "@iconify/react";
import type { EditableTextBlock } from "@/types";
import { rgbToHex } from "@/features/editor/canvas";

const FONT_PRESETS = [
  {
    label: "Comic Neue (Recommended)",
    value: "'Comic Neue', cursive, sans-serif",
  },
  { label: "Bangers (Action/SFX)", value: "'Bangers', cursive, sans-serif" },
  { label: "Anime Ace", value: "'Anime Ace', 'Comic Neue', sans-serif" },
  { label: "Wild Words", value: "'Wild Words', 'Comic Neue', sans-serif" },
  { label: "Action Man", value: "'Action Man', cursive, sans-serif" },
  { label: "Inter (Clean Modern)", value: "Inter, sans-serif" },
  { label: "Impact (Bold Display)", value: "Impact, sans-serif" },
  { label: "Georgia (Narrator/Serif)", value: "Georgia, serif" },
];

function hexToRgb(hex: string): [number, number, number] {
  let c = hex.replace("#", "");
  if (c.length === 3) {
    c = c
      .split("")
      .map((x) => x + x)
      .join("");
  }
  const num = parseInt(c, 16);
  if (isNaN(num)) return [0, 0, 0];
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
}

interface EditorStyleTabProps {
  selectedBlock: EditableTextBlock;
  updateSelectedBlock: (updates: Partial<EditableTextBlock>) => void;
}

export const EditorStyleTab: React.FC<EditorStyleTabProps> = ({
  selectedBlock,
  updateSelectedBlock,
}) => (
  <div className="space-y-4">
    {/* Font Family */}
    <div>
      <label className="block text-xs font-semibold text-zinc-300 mb-1">
        Font Family
      </label>
      <select
        value={selectedBlock.font_family}
        onChange={(e) => updateSelectedBlock({ font_family: e.target.value })}
        className="w-full rounded-lg bg-zinc-950 border border-zinc-700 px-3 py-2 text-xs text-zinc-100 focus:outline-none focus:border-indigo-500"
      >
        {FONT_PRESETS.map((f) => (
          <option key={f.value} value={f.value}>
            {f.label}
          </option>
        ))}
      </select>
    </div>

    {/* Font Size */}
    <div>
      <div className="flex justify-between items-center text-xs text-zinc-300 mb-1">
        <span className="font-semibold">Font Size</span>
        <span className="font-mono text-zinc-400">
          {selectedBlock.font_size} px
        </span>
      </div>
      <input
        type="range"
        min="10"
        max="90"
        step="1"
        value={selectedBlock.font_size}
        onChange={(e) =>
          updateSelectedBlock({
            font_size: Number(e.target.value),
          })
        }
        className="w-full accent-indigo-500 cursor-pointer"
      />
    </div>

    {/* Text Color (Fill) */}
    <div>
      <label className="block text-xs font-semibold text-zinc-300 mb-1.5">
        Text Fill Color
      </label>
      <div className="flex items-center space-x-2">
        <input
          type="color"
          value={rgbToHex(selectedBlock.fg_color)}
          onChange={(e) =>
            updateSelectedBlock({
              fg_color: hexToRgb(e.target.value),
            })
          }
          className="w-8 h-8 rounded border border-zinc-700 bg-zinc-950 cursor-pointer"
        />
        <span className="font-mono text-xs text-zinc-300">
          {rgbToHex(selectedBlock.fg_color).toUpperCase()}
        </span>
        <div className="flex space-x-1 ml-auto">
          <button
            type="button"
            onClick={() => updateSelectedBlock({ fg_color: [0, 0, 0] })}
            className="w-6 h-6 rounded bg-black border border-zinc-600"
            title="Black"
          />
          <button
            type="button"
            onClick={() => updateSelectedBlock({ fg_color: [255, 255, 255] })}
            className="w-6 h-6 rounded bg-white border border-zinc-400"
            title="White"
          />
          <button
            type="button"
            onClick={() => updateSelectedBlock({ fg_color: [239, 68, 68] })}
            className="w-6 h-6 rounded bg-red-500 border border-zinc-600"
            title="Red (SFX)"
          />
        </div>
      </div>
    </div>

    {/* Stroke Outline (Crucial Scanlation Feature) */}
    <div className="p-3 bg-zinc-950 rounded-lg border border-zinc-800 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-zinc-200 flex items-center space-x-1">
          <Icon
            icon="carbon:contour-finding"
            className="w-4 h-4 text-indigo-400"
          />
          <span>Stroke Outline (Contrast)</span>
        </span>
        <span className="font-mono text-xs text-zinc-400">
          {selectedBlock.stroke_width} px
        </span>
      </div>

      <input
        type="range"
        min="0"
        max="8"
        step="0.5"
        value={selectedBlock.stroke_width}
        onChange={(e) =>
          updateSelectedBlock({
            stroke_width: Number(e.target.value),
          })
        }
        className="w-full accent-indigo-500 cursor-pointer"
      />

      <div className="flex items-center space-x-2">
        <input
          type="color"
          value={rgbToHex(selectedBlock.bg_color)}
          onChange={(e) =>
            updateSelectedBlock({
              bg_color: hexToRgb(e.target.value),
            })
          }
          className="w-7 h-7 rounded border border-zinc-700 bg-zinc-950 cursor-pointer"
        />
        <span className="text-xs text-zinc-400 font-mono">
          Stroke: {rgbToHex(selectedBlock.bg_color).toUpperCase()}
        </span>
        <div className="flex space-x-1 ml-auto">
          <button
            type="button"
            onClick={() => updateSelectedBlock({ bg_color: [255, 255, 255] })}
            className="w-5 h-5 rounded bg-white border border-zinc-400"
            title="White Stroke"
          />
          <button
            type="button"
            onClick={() => updateSelectedBlock({ bg_color: [0, 0, 0] })}
            className="w-5 h-5 rounded bg-black border border-zinc-600"
            title="Black Stroke"
          />
        </div>
      </div>
    </div>

    {/* Bold & Italic */}
    <div>
      <span className="block text-xs font-semibold text-zinc-300 mb-1.5">
        Style Emphasis
      </span>
      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => updateSelectedBlock({ bold: !selectedBlock.bold })}
          className={`py-1.5 px-3 rounded flex items-center justify-center space-x-1 text-xs font-bold border ${
            selectedBlock.bold
              ? "bg-indigo-600 border-indigo-500 text-white"
              : "bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700"
          }`}
        >
          <span>B</span>
          <span className="text-[10px] font-normal">Bold</span>
        </button>
        <button
          type="button"
          onClick={() =>
            updateSelectedBlock({
              italic: !selectedBlock.italic,
            })
          }
          className={`py-1.5 px-3 rounded flex items-center justify-center space-x-1 text-xs italic border ${
            selectedBlock.italic
              ? "bg-indigo-600 border-indigo-500 text-white"
              : "bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700"
          }`}
          title="Italic (Standard for Thoughts/Whispers)"
        >
          <span>I</span>
          <span className="text-[10px] font-normal not-italic">
            Italic (Thoughts)
          </span>
        </button>
      </div>
    </div>
  </div>
);
