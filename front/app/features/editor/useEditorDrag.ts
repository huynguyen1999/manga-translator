import { useCallback, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { EditableTextBlock } from "@/types";

export type EditorDragState = {
  type: "move" | "resize" | null;
  handle?: string;
  startX: number;
  startY: number;
  initialBlock: EditableTextBlock | null;
  segmentIndex?: number;
};

export function getEditorDragGeometry(
  dragState: EditorDragState,
  clientX: number,
  clientY: number,
  zoom: number,
): Partial<EditableTextBlock> | null {
  if (!dragState.type || !dragState.initialBlock) return null;

  const deltaX = (clientX - dragState.startX) / zoom;
  const deltaY = (clientY - dragState.startY) / zoom;
  const initial = dragState.initialBlock;
  if (dragState.type === "move") {
    return {
      x: Math.round(initial.x + deltaX),
      y: Math.round(initial.y + deltaY),
      layout_bounds: undefined,
    };
  }

  let x = initial.x;
  let y = initial.y;
  let width = initial.width;
  let height = initial.height;

  switch (dragState.handle) {
    case "se":
      width = Math.max(30, initial.width + deltaX);
      height = Math.max(20, initial.height + deltaY);
      break;
    case "sw":
      width = Math.max(30, initial.width - deltaX);
      x = initial.x + (initial.width - width);
      height = Math.max(20, initial.height + deltaY);
      break;
    case "ne":
      width = Math.max(30, initial.width + deltaX);
      height = Math.max(20, initial.height - deltaY);
      y = initial.y + (initial.height - height);
      break;
    case "nw":
      width = Math.max(30, initial.width - deltaX);
      x = initial.x + (initial.width - width);
      height = Math.max(20, initial.height - deltaY);
      y = initial.y + (initial.height - height);
      break;
    case "e":
      width = Math.max(30, initial.width + deltaX);
      break;
    case "s":
      height = Math.max(20, initial.height + deltaY);
      break;
    case "w":
      width = Math.max(30, initial.width - deltaX);
      x = initial.x + (initial.width - width);
      break;
    case "n":
      height = Math.max(20, initial.height - deltaY);
      y = initial.y + (initial.height - height);
      break;
  }

  return {
    x: Math.round(x),
    y: Math.round(y),
    width: Math.round(width),
    height: Math.round(height),
    layout_bounds: undefined,
  };
}

export function useEditorDrag({
  selectedId,
  setSelectedId,
  zoom,
  commitBlocks,
  pushHistoryCheckpoint,
}: {
  selectedId: string | null;
  setSelectedId: Dispatch<SetStateAction<string | null>>;
  zoom: number;
  commitBlocks: (updater: (previous: EditableTextBlock[]) => EditableTextBlock[], recordHistory?: boolean) => void;
  pushHistoryCheckpoint: () => void;
}) {
  const isDraggingRef = useRef(false);
  const [dragState, setDragState] = useState<EditorDragState>({
    type: null,
    startX: 0,
    startY: 0,
    initialBlock: null,
  });

  const handlePointerDown = (
    event: React.PointerEvent,
    block: EditableTextBlock,
    handle?: string,
    segmentIndex?: number,
  ) => {
    event.stopPropagation();
    setSelectedId(block.id);
    pushHistoryCheckpoint();
    isDraggingRef.current = true;
    setDragState({
      type: handle ? "resize" : "move",
      handle,
      startX: event.clientX,
      startY: event.clientY,
      initialBlock: { ...block },
      segmentIndex,
    });
  };

  const handlePointerMove = useCallback((event: React.PointerEvent) => {
    const geometry = getEditorDragGeometry(dragState, event.clientX, event.clientY, zoom);
    if (!geometry) return;

    if (dragState.segmentIndex === undefined) {
      if (!selectedId) return;
      commitBlocks(
        (previous) => previous.map((block) =>
          block.id === selectedId ? { ...block, ...geometry } : block
        ),
        !isDraggingRef.current,
      );
      return;
    }

    commitBlocks(
      (previous) => previous.map((owner) => {
        if (owner.id !== selectedId || !owner.layout_segments) return owner;
        return {
          ...owner,
          layout_segments: owner.layout_segments.map((segment, index) =>
            index === dragState.segmentIndex
              ? { ...segment, ...geometry, rendered_png: null }
              : segment,
          ),
        };
      }),
      false,
    );
  }, [commitBlocks, dragState, selectedId, zoom]);

  const handlePointerUp = () => {
    isDraggingRef.current = false;
    setDragState({
      type: null,
      startX: 0,
      startY: 0,
      initialBlock: null,
      segmentIndex: undefined,
    });
  };

  return { isDraggingRef, handlePointerDown, handlePointerMove, handlePointerUp };
}
