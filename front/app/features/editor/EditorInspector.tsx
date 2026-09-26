import React from "react";
import { Icon } from "@iconify/react";
import type { EditableTextBlock } from "@/types";
import { EditorTextTab } from "@/features/editor/EditorTextTab";
import { EditorStyleTab } from "@/features/editor/EditorStyleTab";
import { EditorLayoutTab } from "@/features/editor/EditorLayoutTab";

export type EditorTab = "text" | "style" | "layout";

const REVIEW_REASON_COPY: Record<string, { label: string; guidance: string }> =
  {
    text_does_not_fit: {
      label: "Translation does not fit the bubble",
      guidance:
        "Shorten the translation or resize the box, then replace the preserved original.",
    },
    uncertain_cleanup: {
      label: "Original lettering could not be safely cleared",
      guidance:
        "Check the background before replacing it, or accept the preserved original.",
    },
    uncertain_boundary: {
      label: "Bubble boundary is uncertain",
      guidance:
        "Reposition the box to align with the speech bubble, then replace the preserved original.",
    },
    translation_validation_failed: {
      label: "Translation failed validation",
      guidance:
        "Edit the translation until it is complete and readable, then replace the preserved original.",
    },
    low_confidence_story_boundary: {
      label: "Story boundary confidence is low",
      guidance:
        "Check this page against the surrounding pages before replacing the preserved original.",
    },
    response_truncated: {
      label: "Translation response was cut off",
      guidance:
        "The AI hit its output limit mid-response. This region was not translated in that batch. Edit the translation manually, then replace the preserved original.",
    },
  };

export function getReviewReasonCopy(reason?: string | null): {
  label: string;
  guidance: string;
} {
  if (reason && REVIEW_REASON_COPY[reason]) return REVIEW_REASON_COPY[reason];
  const label = reason
    ? reason
        .replace(/[:_]+/g, " ")
        .replace(/\b\w/g, (character) => character.toUpperCase())
    : "Manual review required";
  return {
    label,
    guidance:
      "Check the preserved original and either replace it with this translation or accept the original.",
  };
}

interface EditorInspectorProps {
  selectedBlock: EditableTextBlock | null;
  activeTab: EditorTab;
  setActiveTab: React.Dispatch<React.SetStateAction<EditorTab>>;
  updateSelectedBlock: (updates: Partial<EditableTextBlock>) => void;
  handleFitText: () => void;
  handleDuplicateBubble: () => void;
  handleDeleteBubble: (id: string) => void;
  handleAddBubble: () => void;
}

export const EditorInspector: React.FC<EditorInspectorProps> = ({
  selectedBlock,
  activeTab,
  setActiveTab,
  updateSelectedBlock,
  handleFitText,
  handleDuplicateBubble,
  handleDeleteBubble,
  handleAddBubble,
}) => (
  <div className="min-w-0 max-h-[42vh] md:max-h-none border-t md:border-t-0 md:border-l border-zinc-800 bg-zinc-900 flex flex-col z-20 select-none">
    {/* Tabs */}
    <div className="flex border-b border-zinc-800 text-xs font-medium">
      <button
        type="button"
        onClick={() => setActiveTab("text")}
        className={`flex-1 py-3 text-center transition-colors border-b-2 ${
          activeTab === "text"
            ? "border-indigo-500 text-white bg-zinc-800/50"
            : "border-transparent text-zinc-400 hover:text-zinc-200"
        }`}
      >
        Text
      </button>
      <button
        type="button"
        onClick={() => setActiveTab("style")}
        className={`flex-1 py-3 text-center transition-colors border-b-2 ${
          activeTab === "style"
            ? "border-indigo-500 text-white bg-zinc-800/50"
            : "border-transparent text-zinc-400 hover:text-zinc-200"
        }`}
      >
        Style & Fonts
      </button>
      <button
        type="button"
        onClick={() => setActiveTab("layout")}
        className={`flex-1 py-3 text-center transition-colors border-b-2 ${
          activeTab === "layout"
            ? "border-indigo-500 text-white bg-zinc-800/50"
            : "border-transparent text-zinc-400 hover:text-zinc-200"
        }`}
      >
        Layout
      </button>
    </div>

    <div className="flex-1 overflow-y-auto p-4 space-y-5">
      {selectedBlock ? (
        <>
          {selectedBlock.review_required &&
            (() => {
              const reason = getReviewReasonCopy(selectedBlock.review_reason);
              return (
                <div
                  role="status"
                  className="rounded-lg border border-amber-700 bg-amber-950/40 p-3 text-sm text-amber-100"
                >
                  <p className="font-semibold">Review reason: {reason.label}</p>
                  <p className="mt-2 text-amber-100/90">{reason.guidance}</p>
                  <div className="mt-3 grid gap-2">
                    <button
                      type="button"
                      className="w-full rounded border border-amber-600 px-3 py-2 text-left font-semibold hover:bg-amber-900/50"
                      onClick={() =>
                        updateSelectedBlock({
                          review_required: false,
                          review_reason: null,
                          cover_background: true,
                        })
                      }
                    >
                      Replace original with this translation
                    </button>
                    <button
                      type="button"
                      className="w-full rounded border border-zinc-600 px-3 py-2 text-left text-zinc-200 hover:bg-zinc-800"
                      onClick={() =>
                        updateSelectedBlock({
                          translation: "",
                          review_required: false,
                          review_reason: null,
                          cover_background: false,
                        })
                      }
                    >
                      Accept preserved original
                    </button>
                  </div>
                </div>
              );
            })()}
          {/* TAB 1: TEXT CONTENT */}
          {activeTab === "text" && (
            <EditorTextTab
              selectedBlock={selectedBlock}
              updateSelectedBlock={updateSelectedBlock}
              handleFitText={handleFitText}
            />
          )}

          {/* TAB 2: STYLE, FONTS, COLORS & STROKES */}
          {activeTab === "style" && (
            <EditorStyleTab
              selectedBlock={selectedBlock}
              updateSelectedBlock={updateSelectedBlock}
            />
          )}

          {/* TAB 3: LAYOUT, SPACING & ROTATION */}
          {activeTab === "layout" && (
            <EditorLayoutTab
              selectedBlock={selectedBlock}
              updateSelectedBlock={updateSelectedBlock}
            />
          )}

          {/* Operations Footer */}
          <div className="pt-3 border-t border-zinc-800 flex items-center justify-between">
            <button
              type="button"
              onClick={handleDuplicateBubble}
              className="px-2.5 py-1.5 bg-zinc-800 hover:bg-zinc-700 rounded text-xs text-zinc-300 flex items-center space-x-1"
            >
              <Icon icon="carbon:copy" className="w-3.5 h-3.5" />
              <span>Duplicate</span>
            </button>

            <button
              type="button"
              onClick={() => handleDeleteBubble(selectedBlock.id)}
              className="px-2.5 py-1.5 bg-red-950/60 hover:bg-red-900 border border-red-800/60 rounded text-xs text-red-300 flex items-center space-x-1"
            >
              <Icon icon="carbon:trash-can" className="w-3.5 h-3.5" />
              <span>Delete Bubble</span>
            </button>
          </div>
        </>
      ) : (
        <div className="h-full flex flex-col items-center justify-center text-center p-4 text-zinc-500 space-y-3">
          <Icon icon="carbon:touch-1" className="w-10 h-10 stroke-1" />
          <p className="text-xs">
            Click any speech bubble on the canvas to edit its text, font, color,
            size, stroke, and position.
          </p>
          <button
            type="button"
            onClick={handleAddBubble}
            className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold"
          >
            + Add New Bubble
          </button>
        </div>
      )}
    </div>
  </div>
);
