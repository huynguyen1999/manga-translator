import { useCallback, useEffect, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import type { EditableTextBlock } from "@/types";

export function appendEditorHistory(history: EditableTextBlock[][], snapshot: EditableTextBlock[]) {
  return [...history.slice(-49), snapshot];
}

export function undoEditorHistory(
  past: EditableTextBlock[][],
  current: EditableTextBlock[],
) {
  const previous = past[past.length - 1];
  return previous
    ? { current: previous, past: past.slice(0, -1) }
    : null;
}

export function redoEditorHistory(
  future: EditableTextBlock[][],
  current: EditableTextBlock[],
) {
  const next = future[0];
  return next
    ? { current: next, future: future.slice(1) }
    : null;
}

export function useEditorHistory(
  blocksRef: MutableRefObject<EditableTextBlock[]>,
  setBlocks: Dispatch<SetStateAction<EditableTextBlock[]>>,
  setIsDirty: Dispatch<SetStateAction<boolean>>,
) {
  const [past, setPast] = useState<EditableTextBlock[][]>([]);
  const [future, setFuture] = useState<EditableTextBlock[][]>([]);

  const resetHistory = useCallback(() => {
    setPast([]);
    setFuture([]);
  }, []);

  const commitBlocks = useCallback(
    (updater: (previous: EditableTextBlock[]) => EditableTextBlock[], recordHistory = true) => {
      const previous = blocksRef.current;
      const next = updater(previous);
      if (JSON.stringify(previous) === JSON.stringify(next)) return;
      if (recordHistory) setPast((history) => appendEditorHistory(history, previous));
      setFuture([]);
      blocksRef.current = next;
      setBlocks(next);
      setIsDirty(true);
    },
    [blocksRef, setBlocks, setIsDirty],
  );

  const pushHistoryCheckpoint = useCallback(() => {
    setPast((history) => appendEditorHistory(history, blocksRef.current));
    setFuture([]);
  }, [blocksRef]);

  const undo = useCallback(() => {
    setPast((history) => {
      const current = blocksRef.current;
      const transition = undoEditorHistory(history, current);
      if (!transition) return history;
      blocksRef.current = transition.current;
      setFuture((redo) => [current, ...redo].slice(0, 50));
      setBlocks(transition.current);
      setIsDirty(true);
      return transition.past;
    });
  }, [blocksRef, setBlocks, setIsDirty]);

  const redo = useCallback(() => {
    setFuture((history) => {
      const current = blocksRef.current;
      const transition = redoEditorHistory(history, current);
      if (!transition) return history;
      blocksRef.current = transition.current;
      setPast((undoHistory) => appendEditorHistory(undoHistory, current));
      setBlocks(transition.current);
      setIsDirty(true);
      return transition.future;
    });
  }, [blocksRef, setBlocks, setIsDirty]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      const key = event.key.toLowerCase();
      if (key === "y" || (key === "z" && event.shiftKey)) {
        event.preventDefault();
        redo();
        return;
      }
      if (key !== "z") return;
      event.preventDefault();
      undo();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [redo, undo]);

  return { past, future, commitBlocks, pushHistoryCheckpoint, undo, redo, resetHistory };
}
