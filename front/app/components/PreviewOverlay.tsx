import React, { useState } from "react";
import { Icon } from "@iconify/react";
import type { EditableTextBlock } from "@/types";
import type { DetectedBubbleRegion, DetectedRegionLine } from "@/utils/textRegions";
import type { DetectedPanelRegion } from "@/utils/panelRegions";
import { getConfidenceStyle, getDetectionPaintOrder } from "./PreviewImage";
import { BlockInspectionCard } from "./BlockInspectionCard";

interface PreviewOverlayProps {
  imageCoordinateSize: { width: number; height: number };
  bubbleCoordinateSize?: { width: number; height: number };
  panelCoordinateSize?: { width: number; height: number };
  detectionCoordinateSize: { width: number; height: number };
  showBubbleRegions?: boolean;
  detectedBubbleRegions: DetectedBubbleRegion[];
  selectedBubbleId: string | null;
  onSelectBubbleId: (id: string | null) => void;
  showPanels?: boolean;
  detectedPanelRegions: DetectedPanelRegion[];
  selectedPanelId: string | null;
  onSelectPanelId: (id: string | null) => void;
  showOriginalRegions?: boolean;
  hasOriginalRegionData?: boolean;
  originalTextLines: DetectedRegionLine[];
  hoveredRegionIndex: number | null;
  onHoverRegionIndex: (index: number | null) => void;
  selectedRegionIndex: number | null;
  onSelectRegionIndex: (index: number | null) => void;
  showBubbleBoxes?: boolean;
  effectiveBlocks: EditableTextBlock[];
  selectedBlockId: string | null;
  onSelectBlockId: (id: string | null) => void;
  copiedKind: "original" | "translation" | "id" | null;
  onCopy: (text: string, kind: "original" | "translation" | "id") => void;
}

export const PreviewOverlay: React.FC<PreviewOverlayProps> = ({
  imageCoordinateSize,
  bubbleCoordinateSize,
  panelCoordinateSize,
  detectionCoordinateSize,
  showBubbleRegions,
  detectedBubbleRegions,
  selectedBubbleId,
  onSelectBubbleId,
  showPanels,
  detectedPanelRegions,
  selectedPanelId,
  onSelectPanelId,
  showOriginalRegions,
  hasOriginalRegionData,
  originalTextLines,
  hoveredRegionIndex,
  onHoverRegionIndex,
  selectedRegionIndex,
  onSelectRegionIndex,
  showBubbleBoxes,
  effectiveBlocks,
  selectedBlockId,
  onSelectBlockId,
  copiedKind,
  onCopy,
}) => {
  const [hoveredPanelId, setHoveredPanelId] = useState<string | null>(null);

  const selectedBlockIndex = selectedBlockId ? effectiveBlocks.findIndex((b) => b.id === selectedBlockId) : -1;
  const selectedBlock = selectedBlockIndex !== -1 ? effectiveBlocks[selectedBlockIndex] : null;
  const activePanelCoordinateSize = panelCoordinateSize ?? imageCoordinateSize;

  const clearSelection = () => {
    onSelectPanelId(null);
    onSelectBubbleId(null);
    onSelectRegionIndex(null);
    onSelectBlockId(null);
  };

  return (
    <>
      {/* 1. Panel Detections Layer (Cyan boundary polygons/rects with #1, #2 order badges) */}
      {showPanels && detectedPanelRegions.length > 0 && (
        <svg
          role="img"
          aria-label={`${detectedPanelRegions.length} detected manga panels`}
          className="absolute inset-0 h-full w-full pointer-events-none"
          viewBox={`0 0 ${activePanelCoordinateSize.width} ${activePanelCoordinateSize.height}`}
          preserveAspectRatio="none"
        >
          {detectedPanelRegions.map((panel) => {
            const isSelected = selectedPanelId === panel.id;
            const isHovered = hoveredPanelId === panel.id;
            const [x1, y1, x2, y2] = panel.xyxy;
            const pw = Math.max(1, x2 - x1);
            const ph = Math.max(1, y2 - y1);
            const polygon = panel.polygons && panel.polygons.length > 0 ? panel.polygons[0] : null;
            const badgeX = x1 + 6;
            const badgeY = y1 + 18;

            const handlePanelClick = (e: React.MouseEvent) => {
              e.stopPropagation();
              onSelectPanelId(isSelected ? null : panel.id);
              onSelectBubbleId(null);
              onSelectRegionIndex(null);
              onSelectBlockId(null);
            };

            return (
              <g key={`panel-${panel.id}`}>
                {polygon && polygon.length >= 3 ? (
                  <polygon
                    points={polygon.map(([x, y]) => `${x},${y}`).join(" ")}
                    fill="#06b6d4"
                    fillOpacity={isSelected ? 0.24 : isHovered ? 0.14 : 0.06}
                    stroke={isSelected ? "#22d3ee" : "#0891b2"}
                    strokeOpacity={isSelected ? 1 : 0.85}
                    strokeWidth={isSelected ? 3.5 : 2}
                    vectorEffect="non-scaling-stroke"
                    pointerEvents="all"
                    role="button"
                    tabIndex={0}
                    aria-label={`Panel ${panel.order}`}
                    className="cursor-pointer transition-all duration-150"
                    onMouseEnter={() => setHoveredPanelId(panel.id)}
                    onMouseLeave={() => setHoveredPanelId(null)}
                    onClick={handlePanelClick}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelectPanelId(isSelected ? null : panel.id);
                      }
                    }}
                  >
                    <title>{`Panel #${panel.order} (${pw}x${ph}px)`}</title>
                  </polygon>
                ) : (
                  <rect
                    x={x1}
                    y={y1}
                    width={pw}
                    height={ph}
                    fill="#06b6d4"
                    fillOpacity={isSelected ? 0.24 : isHovered ? 0.14 : 0.06}
                    stroke={isSelected ? "#22d3ee" : "#0891b2"}
                    strokeOpacity={isSelected ? 1 : 0.85}
                    strokeWidth={isSelected ? 3.5 : 2}
                    vectorEffect="non-scaling-stroke"
                    pointerEvents="all"
                    role="button"
                    tabIndex={0}
                    aria-label={`Panel ${panel.order}`}
                    className="cursor-pointer transition-all duration-150"
                    onMouseEnter={() => setHoveredPanelId(panel.id)}
                    onMouseLeave={() => setHoveredPanelId(null)}
                    onClick={handlePanelClick}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelectPanelId(isSelected ? null : panel.id);
                      }
                    }}
                  >
                    <title>{`Panel #${panel.order} (${pw}x${ph}px)`}</title>
                  </rect>
                )}

                {/* Panel Reading Order Badge (#1, #2, ...) */}
                <rect
                  x={badgeX - 4}
                  y={badgeY - 14}
                  width={32}
                  height={18}
                  rx={3}
                  fill="#09090b"
                  stroke={isSelected ? "#22d3ee" : "#0891b2"}
                  strokeWidth={1.5}
                />
                <text
                  x={badgeX + 12}
                  y={badgeY}
                  fill="#ecfeff"
                  fontSize={12}
                  fontWeight={800}
                  fontFamily="system-ui, sans-serif"
                  textAnchor="middle"
                  className="select-none pointer-events-none"
                >
                  #{panel.order}
                </text>
              </g>
            );
          })}
        </svg>
      )}

      {/* 2. Speech Bubble Regions (Violet / Magenta) */}
      {showBubbleRegions && detectedBubbleRegions.length > 0 && (
        <svg
          role="img"
          aria-label={`${detectedBubbleRegions.length} detected speech bubbles`}
          className="absolute inset-0 h-full w-full pointer-events-none"
          viewBox={`0 0 ${bubbleCoordinateSize?.width ?? imageCoordinateSize.width} ${bubbleCoordinateSize?.height ?? imageCoordinateSize.height}`}
          preserveAspectRatio="none"
        >
          {detectedBubbleRegions.flatMap((region) => region.polygons.map((polygon, polygonIdx) => {
            const labelX = Math.min(...polygon.map(([x]) => x));
            const labelY = Math.max(16, Math.min(...polygon.map(([, y]) => y)) - 5);
            return (
              <g key={`${region.id}-${polygonIdx}`}>
                <polygon
                  points={polygon.map(([x, y]) => `${x},${y}`).join(" ")}
                  fill="#a78bfa"
                  fillOpacity={selectedBubbleId === region.id ? 0.22 : 0.04}
                  stroke="#c084fc"
                  strokeOpacity={selectedBubbleId === region.id ? 1 : 0.65}
                  strokeWidth={selectedBubbleId === region.id ? 3 : 1.5}
                  vectorEffect="non-scaling-stroke"
                  pointerEvents="all"
                  role="button"
                  tabIndex={0}
                  aria-label={`Speech bubble ${region.id}`}
                  className="cursor-pointer"
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelectBubbleId(selectedBubbleId === region.id ? null : region.id);
                    onSelectPanelId(null);
                    onSelectRegionIndex(null);
                    onSelectBlockId(null);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelectBubbleId(selectedBubbleId === region.id ? null : region.id);
                    }
                  }}
                >
                  <title>{region.id}</title>
                </polygon>
                {selectedBubbleId === region.id && (
                  <text x={labelX} y={labelY} fill="white" stroke="#18181b" strokeWidth={4}
                    paintOrder="stroke" fontSize={18} fontWeight={700} fontFamily="monospace">{region.id}</text>
                )}
              </g>
            );
          }))}
        </svg>
      )}

      {/* 3. Original Text Detection Polygons */}
      {showOriginalRegions && hasOriginalRegionData && (
        <>
          <svg
            aria-hidden="true"
            className="absolute inset-0 h-full w-full pointer-events-none"
            viewBox={`0 0 ${detectionCoordinateSize.width} ${detectionCoordinateSize.height}`}
            preserveAspectRatio="none"
          >
            {getDetectionPaintOrder(originalTextLines).map((lineIdx) => {
              const region = originalTextLines[lineIdx];
              const line = region.points;
              if (line.length < 3) return null;
              const confStyle = getConfidenceStyle(region.confidence);
              const isHovered = hoveredRegionIndex === lineIdx || selectedRegionIndex === lineIdx;
              const labelX = Math.min(...line.map(([x]) => x));
              const labelY = Math.max(16, Math.min(...line.map(([, y]) => y)) - 5);

              return (
                <g key={`source-${region.id}-${lineIdx}`}>
                  <polygon
                    points={line.map(([x, y]) => `${x},${y}`).join(" ")}
                    fill={isHovered ? confStyle.fill : "transparent"}
                    fillOpacity={isHovered ? 0.25 : 0}
                    stroke={confStyle.stroke}
                    strokeDasharray={isHovered ? "none" : "6 4"}
                    strokeOpacity={isHovered ? 1 : 0.75}
                    strokeWidth={isHovered ? 2.5 : 1.5}
                    vectorEffect="non-scaling-stroke"
                    pointerEvents="all"
                    role="button"
                    tabIndex={0}
                    aria-label={`Original region ${region.id}`}
                    className="pointer-events-auto cursor-pointer transition-all duration-150"
                    onMouseEnter={() => onHoverRegionIndex(lineIdx)}
                    onMouseLeave={() => onHoverRegionIndex(null)}
                    onClick={(event) => {
                      event.stopPropagation();
                      onSelectRegionIndex(selectedRegionIndex === lineIdx ? null : lineIdx);
                      onSelectPanelId(null);
                      onSelectBubbleId(null);
                      onSelectBlockId(null);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onSelectRegionIndex(selectedRegionIndex === lineIdx ? null : lineIdx);
                      }
                    }}
                  />
                  {selectedRegionIndex === lineIdx && (
                    <text x={labelX} y={labelY} fill="white" stroke="#18181b" strokeWidth={4}
                      paintOrder="stroke" fontSize={18} fontWeight={700} fontFamily="monospace">{region.id}</text>
                  )}
                </g>
              );
            })}
          </svg>

          {selectedRegionIndex !== null && (() => {
            const region = originalTextLines[selectedRegionIndex];
            if (!region || region.points.length < 3) return null;
            const xs = region.points.map(([x]) => x);
            const ys = region.points.map(([, y]) => y);
            const midX = (Math.min(...xs) + Math.max(...xs)) / 2;
            const leftPct = (midX / detectionCoordinateSize.width) * 100;
            const topPct = (Math.min(...ys) / detectionCoordinateSize.height) * 100;
            const conf = region.confidence;
            const confStyle = getConfidenceStyle(conf);
            const hasConf = typeof conf === "number" && !isNaN(conf);

            return (
              <div
                key={`selected-region-${selectedRegionIndex}`}
                className="absolute pointer-events-auto select-none z-30 animate-in fade-in zoom-in-95 duration-100"
                style={{ left: `${leftPct}%`, top: `${topPct}%`, transform: "translate(-50%, -100%) translateY(-6px)" }}
              >
                <div className="flex items-center gap-1.5 rounded-md bg-zinc-950/95 border border-zinc-700/80 px-2 py-1 text-[11px] font-medium text-zinc-100 shadow-xl backdrop-blur-md whitespace-nowrap">
                  <span className={`w-2 h-2 rounded-full shrink-0 ${confStyle.dot}`} />
                  <span className="text-zinc-400 font-mono text-[10px]">{region.id}</span>
                  {hasConf ? (
                    <span className="font-mono font-bold text-white">{(conf * 100).toFixed(conf * 100 % 1 === 0 ? 0 : 1)}%</span>
                  ) : (
                    <span className="text-zinc-300">Detected</span>
                  )}
                  <span className="text-zinc-500 text-[10px] capitalize">({confStyle.tier})</span>
                  <button type="button" className="ml-1 text-zinc-300 hover:text-white" title="Copy region ID" onClick={(event) => { event.stopPropagation(); onCopy(region.id, "id"); }}>
                    <Icon icon={copiedKind === "id" ? "carbon:checkmark" : "carbon:copy"} className="h-3 w-3" />
                  </button>
                </div>
              </div>
            );
          })()}
        </>
      )}

      {/* 4. Text Region Blocks / Typeset Boxes */}
      {showBubbleBoxes && effectiveBlocks.map((block, idx) => {
        const isSelected = selectedBlockId === block.id;
        const leftPct = (block.x / imageCoordinateSize.width) * 100;
        const topPct = (block.y / imageCoordinateSize.height) * 100;
        const widthPct = (block.width / imageCoordinateSize.width) * 100;
        const heightPct = (block.height / imageCoordinateSize.height) * 100;

        return (
          <div
            key={block.id || idx}
            role="button"
            tabIndex={0}
            aria-label={`Text region ${block.id}`}
            className={`absolute pointer-events-auto rounded cursor-pointer transition-all duration-150 ${
              isSelected
                ? "border-2 border-amber-400 bg-amber-400/25 shadow-[0_0_12px_rgba(251,191,36,0.6)] ring-2 ring-amber-400/50 z-30"
                : "border-2 border-indigo-400/70 hover:border-indigo-300 bg-indigo-500/15 hover:bg-indigo-500/30 hover:shadow-md z-20"
            }`}
            style={{ left: `${leftPct}%`, top: `${topPct}%`, width: `${widthPct}%`, height: `${heightPct}%` }}
            onClick={(e) => {
              e.stopPropagation();
              onSelectBlockId(isSelected ? null : block.id);
              onSelectPanelId(null);
              onSelectRegionIndex(null);
              onSelectBubbleId(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                e.stopPropagation();
                onSelectBlockId(isSelected ? null : block.id);
              }
            }}
            title={`Text region ${block.id}: Click to inspect original & translated text`}
          >
            {isSelected && (
              <span className="absolute -top-2.5 -left-1 max-w-28 truncate text-[9px] font-mono font-bold px-1 rounded shadow-xs select-none bg-amber-400 text-black">
                {block.id}
              </span>
            )}
          </div>
        );
      })}

      {/* 5. Sticky Details Badge for Selected Item */}
      {(selectedRegionIndex !== null || selectedBubbleId !== null || selectedPanelId !== null) && (() => {
        const source = selectedRegionIndex !== null ? originalTextLines[selectedRegionIndex] : null;
        const bubble = selectedBubbleId ? detectedBubbleRegions.find((r) => r.id === selectedBubbleId) : null;
        const panel = selectedPanelId ? detectedPanelRegions.find((p) => p.id === selectedPanelId) : null;
        const id = source?.id ?? bubble?.id ?? panel?.id;
        if (!id) return null;
        const confidence = source?.confidence ?? bubble?.confidence ?? panel?.confidence;
        const label = source ? "Original text" : bubble ? "Speech bubble" : `Panel #${panel?.order ?? ""}`;
        const colorClass = source ? "border-amber-500/40 text-amber-300" : bubble ? "border-purple-500/40 text-purple-300" : "border-cyan-500/40 text-cyan-300";

        return (
          <div className={`absolute left-2 top-2 z-40 flex items-center gap-2 rounded-lg border bg-zinc-950/95 px-3 py-2 text-xs shadow-xl pointer-events-auto ${colorClass}`}>
            <span className="font-semibold text-white">{label}</span>
            <code className="font-mono text-zinc-300">{id}</code>
            {typeof confidence === "number" && <span>{Math.round(confidence * 100)}%</span>}
            {panel && (
              <span className="text-[10px] text-zinc-400 font-mono">
                {panel.xyxy[2] - panel.xyxy[0]}×{panel.xyxy[3] - panel.xyxy[1]}px
              </span>
            )}
            <button type="button" className="flex items-center gap-1 text-zinc-300 hover:text-white" title="Copy ID" onClick={() => void onCopy(id, "id")}>
              <Icon icon={copiedKind === "id" ? "carbon:checkmark" : "carbon:copy"} className="h-3.5 w-3.5" />
              <span>{copiedKind === "id" ? "Copied" : "Copy ID"}</span>
            </button>
            <button type="button" aria-label="Close details" onClick={clearSelection}>
              <Icon icon="carbon:close" className="h-4 w-4 text-zinc-400 hover:text-white" />
            </button>
          </div>
        );
      })()}

      {/* 6. Inspection Card for Selected Text Block */}
      {showBubbleBoxes && selectedBlock && selectedBlockIndex !== -1 && (
        <BlockInspectionCard
          block={selectedBlock}
          index={selectedBlockIndex}
          imageCoordinateSize={imageCoordinateSize}
          copiedKind={copiedKind}
          onCopy={onCopy}
          onClose={() => onSelectBlockId(null)}
        />
      )}
    </>
  );
};
