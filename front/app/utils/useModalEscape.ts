import { useEffect, useRef } from "react";

type EscapeHandler = (event: KeyboardEvent) => void;

const activeHandlers: Array<{ id: symbol; handler: EscapeHandler }> = [];

const handleKeyDown = (event: KeyboardEvent) => {
  if (event.key !== "Escape") return;
  const active = activeHandlers[activeHandlers.length - 1];
  if (!active) return;

  event.preventDefault();
  event.stopImmediatePropagation();
  active.handler(event);
};

export function useModalEscape(active: boolean, onEscape: EscapeHandler) {
  const handlerRef = useRef(onEscape);
  handlerRef.current = onEscape;

  useEffect(() => {
    if (!active) return;
    const entry = { id: Symbol(), handler: (event: KeyboardEvent) => handlerRef.current(event) };
    const wasEmpty = activeHandlers.length === 0;
    activeHandlers.push(entry);
    if (wasEmpty) window.addEventListener("keydown", handleKeyDown, true);

    return () => {
      const index = activeHandlers.findIndex(({ id }) => id === entry.id);
      if (index !== -1) activeHandlers.splice(index, 1);
      if (activeHandlers.length === 0) window.removeEventListener("keydown", handleKeyDown, true);
    };
  }, [active]);
}
