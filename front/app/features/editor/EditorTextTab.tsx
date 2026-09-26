import React from "react";
import { Icon } from "@iconify/react";
import type { EditableTextBlock } from "@/types";

interface EditorTextTabProps {
  selectedBlock: EditableTextBlock;
  updateSelectedBlock: (updates: Partial<EditableTextBlock>) => void;
  handleFitText: () => void;
}

export const EditorTextTab: React.FC<EditorTextTabProps> = ({
  selectedBlock,
  updateSelectedBlock,
  handleFitText,
}) => (
  <div className="space-y-4">
    <div>
      <label className="block text-xs font-semibold text-zinc-300 mb-1">
        Translated Dialogue
      </label>
      <textarea
        rows={4}
        value={selectedBlock.translation}
        onChange={(e) => updateSelectedBlock({ translation: e.target.value })}
        className="w-full rounded-lg bg-zinc-950 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
        placeholder="Type translated text here..."
      />
      <p className="text-[11px] text-zinc-400 mt-1">
        Tip: Press Enter to shape lines to fit the speech bubble egg/oval.
      </p>
      <label className="mt-3 flex items-start gap-2 text-xs text-zinc-300">
        <input
          type="checkbox"
          checked={Boolean(selectedBlock.cover_background)}
          onChange={(e) =>
            updateSelectedBlock({
              cover_background: e.target.checked,
            })
          }
          className="mt-0.5 accent-indigo-500"
        />
        <span>
          Cover existing lettering
          <span className="block text-[11px] text-zinc-500">
            Uses a white box; best for flat speech bubbles.
          </span>
        </span>
      </label>
      <button
        type="button"
        onClick={handleFitText}
        className="mt-2 px-2.5 py-1.5 bg-zinc-800 hover:bg-zinc-700 rounded text-xs text-zinc-300 border border-zinc-700"
        title="Shrink this text until it fits its box"
      >
        Fit text to box
      </button>
    </div>

    {/* Quick Lettering Case Buttons */}
    <div>
      <span className="block text-[11px] font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">
        Lettering Conventions
      </span>
      <div className="grid grid-cols-3 gap-1.5">
        <button
          type="button"
          onClick={() =>
            updateSelectedBlock({
              translation: selectedBlock.translation.toUpperCase(),
            })
          }
          className="px-2 py-1 bg-zinc-800 hover:bg-zinc-700 rounded text-xs text-zinc-200 border border-zinc-700 font-mono"
          title="ALL CAPS (Standard Comic Dialogue)"
        >
          ALL CAPS
        </button>
        <button
          type="button"
          onClick={() => {
            const val = selectedBlock.translation
              .toLowerCase()
              .replace(/(^\s*\w|[.!?]\s*\w)/g, (c) => c.toUpperCase());
            updateSelectedBlock({ translation: val });
          }}
          className="px-2 py-1 bg-zinc-800 hover:bg-zinc-700 rounded text-xs text-zinc-200 border border-zinc-700 font-mono"
          title="Sentence case (Whispers/Narrations)"
        >
          Sentence
        </button>
        <button
          type="button"
          onClick={() =>
            updateSelectedBlock({
              translation: selectedBlock.translation.toLowerCase(),
            })
          }
          className="px-2 py-1 bg-zinc-800 hover:bg-zinc-700 rounded text-xs text-zinc-200 border border-zinc-700 font-mono"
          title="lowercase"
        >
          lower
        </button>
      </div>
    </div>

    {/* Original Source Reference */}
    {selectedBlock.original_text && (
      <div className="p-2.5 rounded-lg bg-zinc-950 border border-zinc-800">
        <span className="text-[10px] uppercase font-mono text-zinc-400 block mb-1">
          Original Text (OCR)
        </span>
        <p className="text-xs font-mono text-zinc-300 select-all">
          {selectedBlock.original_text}
        </p>
      </div>
    )}

    {/* Alignment & Direction */}
    <div>
      <span className="block text-xs font-semibold text-zinc-300 mb-1.5">
        Alignment (Bubble Centering)
      </span>
      <div className="grid grid-cols-3 gap-1 bg-zinc-950 p-1 rounded-lg border border-zinc-800">
        <button
          type="button"
          onClick={() => updateSelectedBlock({ alignment: "left" })}
          className={`py-1 rounded flex justify-center text-xs ${
            selectedBlock.alignment === "left"
              ? "bg-indigo-600 text-white"
              : "text-zinc-400 hover:text-white"
          }`}
        >
          <Icon icon="carbon:text-align-left" className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={() => updateSelectedBlock({ alignment: "center" })}
          className={`py-1 rounded flex justify-center text-xs ${
            selectedBlock.alignment === "center"
              ? "bg-indigo-600 text-white"
              : "text-zinc-400 hover:text-white"
          }`}
          title="Center alignment (Manga standard)"
        >
          <Icon icon="carbon:text-align-center" className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={() => updateSelectedBlock({ alignment: "right" })}
          className={`py-1 rounded flex justify-center text-xs ${
            selectedBlock.alignment === "right"
              ? "bg-indigo-600 text-white"
              : "text-zinc-400 hover:text-white"
          }`}
        >
          <Icon icon="carbon:text-align-right" className="w-4 h-4" />
        </button>
      </div>
    </div>
  </div>
);
