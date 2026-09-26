import React from "react";
import { Icon } from "@iconify/react";
import type { EditableTextBlock, FinishedImage } from "@/types";
import { getHorizontalLines, rgbToHex } from "@/features/editor/canvas";
import { apiUrl } from "@/utils/api";

interface EditorCanvasBlock {
  owner: EditableTextBlock;
  block: EditableTextBlock;
  segmentIndex?: number;
}

interface EditorCanvasStageProps {
  image: FinishedImage;
  canvasContainerRef: React.RefObject<HTMLDivElement | null>;
  imageSize: { width: number; height: number };
  zoom: number;
  bgImageUrl: string | null;
  showOriginalOverlay: boolean;
  loading: boolean;
  visualBlocks: EditorCanvasBlock[];
  selectedId: string | null;
  handlePointerDown: (
    event: React.PointerEvent,
    block: EditableTextBlock,
    handle?: string,
    segmentIndex?: number,
  ) => void;
  handleDeleteBubble: (id: string) => void;
  backgroundError: string | null;
  backgroundNotice: string | null;
  layoutPreviewError: string | null;
  onClearSelection: () => void;
  onRetryLayoutPreview: () => void;
}

export const EditorCanvasStage: React.FC<EditorCanvasStageProps> = ({
  image,
  canvasContainerRef,
  imageSize,
  zoom,
  bgImageUrl,
  showOriginalOverlay,
  loading,
  visualBlocks,
  selectedId,
  handlePointerDown,
  handleDeleteBubble,
  backgroundError,
  backgroundNotice,
  layoutPreviewError,
  onClearSelection,
  onRetryLayoutPreview,
}) => (
  <div
    ref={canvasContainerRef}
    className="min-h-0 min-w-0 flex-1 overflow-auto bg-zinc-950 flex items-start justify-start p-8 relative"
    onClick={() => onClearSelection()}
  >
    {loading ? (
      <div className="absolute inset-0 flex flex-col items-center justify-center space-y-3 text-zinc-400">
        <Icon
          icon="carbon:circle-dash"
          className="w-8 h-8 animate-spin text-indigo-500"
        />
        <p className="text-sm">Loading page layers & typesetting data...</p>
      </div>
    ) : (
      <div
        className="relative shrink-0 mx-auto my-auto select-none"
        style={{
          width: imageSize.width * zoom,
          height: imageSize.height * zoom,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="relative shadow-2xl transition-transform origin-top-left select-none"
          style={{
            width: imageSize.width,
            height: imageSize.height,
            transform: `scale(${zoom})`,
          }}
        >
          {/* Background Clean Inpainted Art Layer */}
          {bgImageUrl ? (
            <img
              src={
                showOriginalOverlay && typeof image.inputUrl === "string"
                  ? apiUrl(image.inputUrl)
                  : bgImageUrl
              }
              alt="Manga Canvas"
              className="w-full h-full object-contain pointer-events-none"
              draggable={false}
            />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center bg-zinc-900 text-center text-zinc-400 p-8">
              <div>
                <Icon
                  icon="carbon:image-search"
                  className="w-10 h-10 mx-auto mb-3 text-amber-400"
                />
                <p className="text-sm font-medium text-zinc-200">
                  Page artwork unavailable
                </p>
                <p className="text-xs mt-1">
                  Text editing is available, but export is disabled until an
                  image can be loaded.
                </p>
              </div>
            </div>
          )}

          {/* Interactive Speech Bubbles / Text Layer */}
          {!showOriginalOverlay &&
            visualBlocks.map(({ owner, block, segmentIndex }) => {
              const isSelected = block.id === selectedId;
              const textColor = rgbToHex(block.fg_color);
              const strokeColor = rgbToHex(block.bg_color);
              const strokePx =
                typeof block.stroke_width === "number" && block.stroke_width > 0
                  ? Math.max(
                      1.5,
                      block.stroke_width >= 1
                        ? block.stroke_width
                        : Math.round(block.font_size * 0.08),
                    )
                  : 0;

              return (
                <div
                  key={`${block.id}:${segmentIndex ?? "single"}`}
                  onPointerDown={(e) =>
                    handlePointerDown(e, block, undefined, segmentIndex)
                  }
                  className={`absolute cursor-move transition-shadow ${
                    isSelected
                      ? "ring-2 ring-indigo-500 ring-offset-2 ring-offset-zinc-950 bg-indigo-500/10 z-20"
                      : "hover:ring-1 hover:ring-indigo-400/50 hover:bg-indigo-500/5 z-10"
                  }`}
                  style={{
                    left: block.x,
                    top: block.y,
                    width: block.width,
                    height: block.height,
                    transform: block.angle
                      ? `rotate(${block.angle}deg)`
                      : undefined,
                    transformOrigin: "center center",
                  }}
                >
                  {block.cover_background && (
                    <div
                      className="absolute inset-0 bg-white pointer-events-none"
                      aria-hidden="true"
                    />
                  )}
                  {block.review_required &&
                    (segmentIndex === undefined || segmentIndex === 0) && (
                      <span className="absolute -top-6 left-0 whitespace-nowrap rounded bg-amber-950 px-2 py-1 text-xs text-amber-100">
                        Needs editing
                      </span>
                    )}
                  {!(
                    segmentIndex !== undefined &&
                    owner.layout_segments?.length &&
                    !block.rendered_png
                  ) && (
                    <div
                      className={`relative w-full h-full overflow-hidden pointer-events-none ${block.positioned_lines?.length ? "" : "flex flex-col justify-center px-2 py-1"}`}
                      style={{
                        fontFamily:
                          block.font_family ||
                          "'Comic Neue', cursive, sans-serif",
                        fontSize: `${block.font_size || 24}px`,
                        color: textColor,
                        textAlign: block.alignment || "center",
                        lineHeight: block.line_spacing ?? 1.15,
                        letterSpacing: `${block.letter_spacing || 0}px`,
                        fontWeight: block.bold ? "bold" : "normal",
                        fontStyle: block.italic ? "italic" : "normal",
                        textShadow:
                          strokePx > 0
                            ? `
                            -${strokePx}px -${strokePx}px 0 ${strokeColor},
                             ${strokePx}px -${strokePx}px 0 ${strokeColor},
                            -${strokePx}px  ${strokePx}px 0 ${strokeColor},
                             ${strokePx}px  ${strokePx}px 0 ${strokeColor},
                            0px -${strokePx}px 0 ${strokeColor},
                            0px  ${strokePx}px 0 ${strokeColor},
                            -${strokePx}px 0px 0 ${strokeColor},
                             ${strokePx}px 0px 0 ${strokeColor}
                          `
                            : undefined,
                        writingMode:
                          block.direction === "v"
                            ? "vertical-rl"
                            : "horizontal-tb",
                      }}
                    >
                      {block.rendered_png ? (
                        <img
                          src={`data:image/png;base64,${block.rendered_png}`}
                          alt=""
                          className="absolute inset-0 w-full h-full"
                        />
                      ) : block.positioned_lines?.length ? (
                        block.positioned_lines.map((line, index) => (
                          <span
                            key={index}
                            className="absolute whitespace-nowrap select-none"
                            style={{
                              left: line.x - block.x,
                              top: line.y - block.y,
                            }}
                          >
                            {line.text}
                          </span>
                        ))
                      ) : (
                        <span className="whitespace-pre-wrap [word-break:normal] [overflow-wrap:normal] select-none">
                          {block.translation
                            ? block.direction === "h"
                              ? getHorizontalLines(block).join("\n")
                              : block.translation
                            : "..."}
                        </span>
                      )}
                    </div>
                  )}

                  {/* Resize Handles (Only shown when selected) */}
                  {isSelected && (
                    <>
                      {/* 8 Resize Points */}
                      <div
                        onPointerDown={(e) =>
                          handlePointerDown(e, block, "nw", segmentIndex)
                        }
                        className="absolute -top-1.5 -left-1.5 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-nwse-resize z-30"
                      />
                      <div
                        onPointerDown={(e) =>
                          handlePointerDown(e, block, "n", segmentIndex)
                        }
                        className="absolute -top-1.5 left-1/2 -translate-x-1/2 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-ns-resize z-30"
                      />
                      <div
                        onPointerDown={(e) =>
                          handlePointerDown(e, block, "ne", segmentIndex)
                        }
                        className="absolute -top-1.5 -right-1.5 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-nesw-resize z-30"
                      />
                      <div
                        onPointerDown={(e) =>
                          handlePointerDown(e, block, "e", segmentIndex)
                        }
                        className="absolute top-1/2 -right-1.5 -translate-y-1/2 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-ew-resize z-30"
                      />
                      <div
                        onPointerDown={(e) =>
                          handlePointerDown(e, block, "se", segmentIndex)
                        }
                        className="absolute -bottom-1.5 -right-1.5 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-nwse-resize z-30"
                      />
                      <div
                        onPointerDown={(e) =>
                          handlePointerDown(e, block, "s", segmentIndex)
                        }
                        className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-ns-resize z-30"
                      />
                      <div
                        onPointerDown={(e) =>
                          handlePointerDown(e, block, "sw", segmentIndex)
                        }
                        className="absolute -bottom-1.5 -left-1.5 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-nesw-resize z-30"
                      />
                      <div
                        onPointerDown={(e) =>
                          handlePointerDown(e, block, "w", segmentIndex)
                        }
                        className="absolute top-1/2 -left-1.5 -translate-y-1/2 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-ew-resize z-30"
                      />

                      {/* Quick Delete Bubble Icon */}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteBubble(owner.id);
                        }}
                        className="absolute -top-7 right-0 p-1 bg-red-600 text-white rounded shadow-md hover:bg-red-500 cursor-pointer"
                        title="Delete Bubble"
                      >
                        <Icon icon="carbon:trash-can" className="w-3.5 h-3.5" />
                      </button>
                    </>
                  )}
                </div>
              );
            })}
        </div>
      </div>
    )}
    {(backgroundError || backgroundNotice) && (
      <div
        role="status"
        className={`absolute top-3 left-3 right-3 z-40 rounded-lg border px-3 py-2 text-xs ${
          backgroundError
            ? "border-amber-500/40 bg-amber-950/80 text-amber-200"
            : "border-zinc-700 bg-zinc-900/90 text-zinc-300"
        }`}
      >
        {backgroundError || backgroundNotice}
      </div>
    )}
    {layoutPreviewError && (
      <div
        role="alert"
        className="absolute bottom-3 left-3 right-3 z-40 flex items-center justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-950/90 px-3 py-2 text-xs text-amber-100"
      >
        <span>
          Linked bubble preview could not refresh. The last valid placement is
          still shown.
        </span>
        <button
          type="button"
          className="shrink-0 rounded-md border border-amber-500/60 px-3 py-1.5 font-semibold text-white hover:bg-amber-900"
          onClick={() => onRetryLayoutPreview()}
        >
          Retry
        </button>
      </div>
    )}
  </div>
);
