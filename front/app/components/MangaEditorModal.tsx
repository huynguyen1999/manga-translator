import React, { useState, useEffect, useRef, useCallback } from "react";
import { Icon } from "@iconify/react";
import type { FinishedImage, EditableTextBlock } from "@/types";
import { getFitZoom, normalizeBlockDimensions, estimateFitFontSize, wrapTextLines } from "./mangaEditorGeometry";
import { apiUrl } from "@/utils/api";



interface MangaEditorModalProps {
  image: FinishedImage;
  onClose: () => void;
  onSave?: (updatedImage: FinishedImage) => void;
}

const FONT_PRESETS = [
  { label: "Comic Neue (Recommended)", value: "'Comic Neue', cursive, sans-serif" },
  { label: "Bangers (Action/SFX)", value: "'Bangers', cursive, sans-serif" },
  { label: "Anime Ace", value: "'Anime Ace', 'Comic Neue', sans-serif" },
  { label: "Wild Words", value: "'Wild Words', 'Comic Neue', sans-serif" },
  { label: "Action Man", value: "'Action Man', cursive, sans-serif" },
  { label: "Inter (Clean Modern)", value: "Inter, sans-serif" },
  { label: "Impact (Bold Display)", value: "Impact, sans-serif" },
  { label: "Georgia (Narrator/Serif)", value: "Georgia, serif" },
];

const REVIEW_REASON_COPY: Record<string, { label: string; guidance: string }> = {
  text_does_not_fit: {
    label: "Translation does not fit the bubble",
    guidance: "Shorten the translation or resize the box, then replace the preserved original.",
  },
  uncertain_cleanup: {
    label: "Original lettering could not be safely cleared",
    guidance: "Check the background before replacing it, or accept the preserved original.",
  },
  uncertain_boundary: {
    label: "Bubble boundary is uncertain",
    guidance: "Reposition the box to align with the speech bubble, then replace the preserved original.",
  },
  translation_validation_failed: {
    label: "Translation failed validation",
    guidance: "Edit the translation until it is complete and readable, then replace the preserved original.",
  },
  low_confidence_story_boundary: {
    label: "Story boundary confidence is low",
    guidance: "Check this page against the surrounding pages before replacing the preserved original.",
  },
  response_truncated: {
    label: "Translation response was cut off",
    guidance:
      "The AI hit its output limit mid-response. This region was not translated in that batch. Edit the translation manually, then replace the preserved original.",
  },
};

function getReviewReasonCopy(reason?: string | null): { label: string; guidance: string } {
  if (reason && REVIEW_REASON_COPY[reason]) return REVIEW_REASON_COPY[reason];
  const label = reason
    ? reason
        .replace(/[:_]+/g, " ")
        .replace(/\b\w/g, (character) => character.toUpperCase())
    : "Manual review required";
  return {
    label,
    guidance: "Check the preserved original and either replace it with this translation or accept the original.",
  };
}

function rgbToHex(rgb: [number, number, number]): string {
  const [r, g, b] = rgb;
  const toHex = (n: number) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function hexToRgb(hex: string): [number, number, number] {
  let c = hex.replace("#", "");
  if (c.length === 3) {
    c = c.split("").map((x) => x + x).join("");
  }
  const num = parseInt(c, 16);
  if (isNaN(num)) return [0, 0, 0];
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
}

function approximateTextWidth(value: string, fontSize: number, letterSpacing: number): number {
  return Array.from(value).length * fontSize * 0.54 + Math.max(0, Array.from(value).length - 1) * Math.max(0, letterSpacing);
}

function getHorizontalLines(
  block: EditableTextBlock,
  text = block.translation,
  width = block.width,
): string[] {
  return wrapTextLines(
    text,
    (value) => approximateTextWidth(value, block.font_size || 24, block.letter_spacing || 0),
    Math.max(1, width - 10),
  );
}

function loadDecodedImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = async () => {
      try {
        await img.decode();
        resolve(img);
      } catch (error) {
        reject(error);
      }
    };
    img.onerror = () => reject(new Error(`Could not load image: ${url}`));
    img.src = url;
  });
}

export const MangaEditorModal: React.FC<MangaEditorModalProps> = ({
  image,
  onClose,
  onSave,
}) => {
  const [blocks, setBlocks] = useState<EditableTextBlock[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [bgImageUrl, setBgImageUrl] = useState<string | null>(null);
  const [imageSize, setImageSize] = useState<{ width: number; height: number }>({ width: 1000, height: 1400 });
  const [loading, setLoading] = useState(true);
  const [backgroundError, setBackgroundError] = useState<string | null>(null);
  const [backgroundNotice, setBackgroundNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [showOriginalOverlay, setShowOriginalOverlay] = useState(false);
  const [activeTab, setActiveTab] = useState<"text" | "style" | "layout">("text");
  const [past, setPast] = useState<EditableTextBlock[][]>([]);
  const [future, setFuture] = useState<EditableTextBlock[][]>([]);
  const [isDirty, setIsDirty] = useState(false);
  const [layoutPreviewError, setLayoutPreviewError] = useState<string | null>(null);
  const [layoutPreviewRetry, setLayoutPreviewRetry] = useState(0);

  const canvasContainerRef = useRef<HTMLDivElement>(null);
  const bgImgRef = useRef<HTMLImageElement | null>(null);
  const blocksRef = useRef<EditableTextBlock[]>([]);
  const isDraggingRef = useRef(false);
  const previewSignaturesRef = useRef<Record<string, string>>({});

  // Dragging / Resizing State
  const [dragState, setDragState] = useState<{
    type: "move" | "resize" | null;
    handle?: string;
    startX: number;
    startY: number;
    initialBlock: EditableTextBlock | null;
    segmentIndex?: number;
  }>({
    type: null,
    startX: 0,
    startY: 0,
    initialBlock: null,
  });

  // Load Background Image and Text Regions
  useEffect(() => {
    let isMounted = true;
    setLoading(true);
    setBgImageUrl(null);
    setBackgroundError(null);
    setBackgroundNotice(null);
    bgImgRef.current = null;

    const loadData = async () => {
      const objectUrls: string[] = [];
      const candidates: { url: string; kind: "inpainted" | "input" | "result" }[] = [];
      const seenUrls = new Set<string>();
      const addCandidate = (url: string | null | undefined, kind: (typeof candidates)[number]["kind"]) => {
        if (url && !seenUrls.has(url)) {
          seenUrls.add(url);
          candidates.push({ url, kind });
        }
      };
      const addBlobCandidate = (value: Blob | null | undefined, kind: (typeof candidates)[number]["kind"]) => {
        if (value) {
          const url = URL.createObjectURL(value);
          objectUrls.push(url);
          addCandidate(url, kind);
        }
      };

      // Try the server's clean layer first, then progressively less ideal sources.
      addCandidate(typeof image.inpaintedUrl === "string" ? apiUrl(image.inpaintedUrl) : image.inpaintedUrl, "inpainted");
      if (image.folder) addCandidate(apiUrl(`/api/result/${image.folder}/inpainted.jpg`), "inpainted");
      if (typeof image.inputUrl === "string") addCandidate(apiUrl(image.inputUrl), "input");
      else addBlobCandidate(image.inputUrl, "input");
      if (typeof image.result === "string") addCandidate(apiUrl(image.result), "result");
      else addBlobCandidate(image.result, "result");

      const loadBackground = async () => {
        for (const candidate of candidates) {
          try {
            const img = await loadDecodedImage(candidate.url);
            return { ...candidate, img };
          } catch {
            // Try the next available source.
          }
        }
        return null;
      };

      const backgroundPromise = loadBackground();

      // Load text_regions.json independently so a slow image does not delay metadata.
      let loadedBlocks: EditableTextBlock[] = [];
      let textRegionsNotice: string | null = null;
      const textRegionsUrl = typeof image.textRegionsUrl === "string"
        ? apiUrl(image.textRegionsUrl)
        : (image.folder ? apiUrl(`/api/result/${image.folder}/text_regions.json`) : null);
      if (textRegionsUrl) {
        try {
          const res = await fetch(textRegionsUrl);
          if (res.ok) {
            const data = await res.json();
            if (Array.isArray(data) && data.length > 0) {
              loadedBlocks = data.map((item, idx) => ({
                id: item.id || `bubble_${idx}`,
                bubble_safe_shape: item.bubble_safe_shape && typeof item.bubble_safe_shape.png === "string"
                  ? item.bubble_safe_shape : null,
                group_members: Array.isArray(item.group_members) ? item.group_members : undefined,
                review_required: item.review_required === true || (Array.isArray(item.layout_segments) && item.layout_segments.length > 1 && item.layout_segments.some((segment: Record<string, unknown>) => typeof segment.rendered_png !== "string")),
                review_reason: typeof item.review_reason === "string" ? item.review_reason : null,
                x: Number(item.x) || 0,
                y: Number(item.y) || 0,
                width: Math.max(40, Number(item.width) || 120),
                height: Math.max(30, Number(item.height) || 80),
                lines: Array.isArray(item.lines) ? item.lines : undefined,
                cover_background: Boolean(item.cover_background),
                translation: String(item.translation || ""),
                original_text: item.original_text || "",
                font_size: Math.max(10, Number(item.font_size) || 24),
                font_family: item.font_family || "'Comic Neue', cursive, sans-serif",
                fg_color: Array.isArray(item.fg_color) ? item.fg_color : [0, 0, 0],
                bg_color: Array.isArray(item.bg_color) ? item.bg_color : [255, 255, 255],
                stroke_width: typeof item.stroke_width === "number" ? item.stroke_width : 3.0,
                angle: Number(item.angle) || 0,
                direction: item.direction === "v" ? "v" : "h",
                alignment: item.alignment || "center",
                line_spacing: item.line_spacing == null ? 1.15 : Number(item.line_spacing),
                letter_spacing: Number(item.letter_spacing) || 0,
                bold: Boolean(item.bold),
                italic: Boolean(item.italic),
                layout_bounds:
                  item.layout_bounds && typeof item.layout_bounds === "object"
                    ? {
                        x: Number(item.layout_bounds.x) || 0,
                        y: Number(item.layout_bounds.y) || 0,
                        width: Math.max(40, Number(item.layout_bounds.width) || 120),
                        height: Math.max(30, Number(item.layout_bounds.height) || 80),
                      }
                    : undefined,
                layout_segments: Array.isArray(item.layout_segments)
                  ? item.layout_segments.map((segment: Record<string, unknown>) => ({
                      x: Number(segment.x) || 0,
                      y: Number(segment.y) || 0,
                      width: Math.max(1, Number(segment.width) || 1),
                      height: Math.max(1, Number(segment.height) || 1),
                      text: String(segment.text || ""),
                      font_size: segment.font_size ? Number(segment.font_size) : undefined,
                      rendered_png: typeof segment.rendered_png === "string" ? segment.rendered_png : null,
                      positioned_lines: Array.isArray(segment.positioned_lines)
                        ? segment.positioned_lines.map((line: Record<string, unknown>) => ({
                            text: String(line.text || ""), x: Number(line.x) || 0, y: Number(line.y) || 0,
                          })) : undefined,
                    }))
                  : undefined,
              }));
            }
          } else {
            textRegionsNotice = `Saved text regions could not be loaded (${res.status}); added a starter bubble.`;
          }
        } catch (err) {
          console.warn("Could not fetch text_regions.json:", err);
          textRegionsNotice = "Saved text regions could not be loaded; added a starter bubble.";
        }
      } else {
        textRegionsNotice = "No saved text regions found; added a starter bubble.";
      }

      // If no blocks were loaded, provide an initial editable bubble
      if (loadedBlocks.length === 0) {
        loadedBlocks = [
          {
            id: "bubble_0",
            x: 100,
            y: 100,
            width: 260,
            height: 140,
            cover_background: false,
            translation: "Click to edit translation text",
            original_text: "",
            font_size: 26,
            font_family: "'Comic Neue', cursive, sans-serif",
            fg_color: [0, 0, 0],
            bg_color: [255, 255, 255],
            stroke_width: 3.5,
            angle: 0,
            direction: "h",
            alignment: "center",
            line_spacing: 1.15,
            letter_spacing: 0,
            bold: false,
            italic: false,
          },
        ];
      }

      const background = await backgroundPromise;
      if (isMounted) {
        const bgW = background?.img.naturalWidth || 1000;
        const bgH = background?.img.naturalHeight || 1400;

        // Normalize narrow vertical boxes into speech bubble proportions and auto-fit font sizes
        const normalizedBlocks = loadedBlocks.map((b) => {
          if (b.review_required || b.group_members?.length) return b;
          const layout = b.layout_bounds || b;
          const norm = normalizeBlockDimensions(
            {
              x: layout.x,
              y: layout.y,
              width: layout.width,
              height: layout.height,
              direction: b.direction,
              translation: b.translation,
            },
            bgW,
            bgH,
          );
          const fittedFontSize = estimateFitFontSize(
            b.translation,
            norm.width,
            norm.height,
            b.font_size,
            b.line_spacing,
          );
          return {
            ...b,
            x: norm.x,
            y: norm.y,
            width: norm.width,
            height: norm.height,
            font_size: fittedFontSize,
            layout_bounds: norm,
          };
        });

        if (background) {
          setBgImageUrl(background.url);
          setImageSize({ width: bgW, height: bgH });
          bgImgRef.current = background.img;
          setBackgroundNotice(
            background.kind === "input"
              ? "Using the original page as a fallback; existing lettering may still be present."
              : background.kind === "result"
                ? "Using the final result as a fallback; existing lettering may be duplicated."
                : null,
          );
        } else {
          setBackgroundError("Page artwork could not be loaded from the available sources.");
        }
        blocksRef.current = normalizedBlocks;
        setBlocks(normalizedBlocks);
        setPast([]);
        setFuture([]);
        setIsDirty(false);
        setSelectedId(normalizedBlocks.find((block) => block.review_required)?.id || normalizedBlocks[0]?.id || null);
        setBackgroundNotice((current) => [current, textRegionsNotice].filter(Boolean).join(" ") || null);
        setLoading(false);
      }


      return () => objectUrls.forEach((url) => URL.revokeObjectURL(url));
    };

    const cleanupPromise = loadData();

    return () => {
      isMounted = false;
      cleanupPromise.then((cleanup) => cleanup?.());
    };
  }, [image]);

  const selectedBlock = blocks.find((b) => b.id === selectedId) || null;
  const visualBlocks = blocks.reduce<Array<{ owner: EditableTextBlock; block: EditableTextBlock; segmentIndex?: number }>>(
    (items, owner) => {
      if (!owner.layout_segments?.length) return [...items, { owner, block: owner }];
      return items.concat(owner.layout_segments.map((segment, segmentIndex) => ({
        owner,
        block: { ...owner, ...segment, translation: segment.text, font_size: segment.font_size ?? owner.font_size, layout_segments: undefined } as EditableTextBlock,
        segmentIndex,
      })));
    },
    [],
  );

  const commitBlocks = useCallback(
    (updater: (previous: EditableTextBlock[]) => EditableTextBlock[], recordHistory = true) => {
      const previous = blocksRef.current;
      const next = updater(previous);
      if (JSON.stringify(previous) === JSON.stringify(next)) return;
      if (recordHistory) setPast((history) => [...history.slice(-49), previous]);
      setFuture([]);
      blocksRef.current = next;
      setBlocks(next);
      setIsDirty(true);
    },
    [],
  );

  useEffect(() => {
    if (!image.folder || loading) return;
    const candidates = blocks.filter((block) => (block.layout_segments?.length || 0) > 1);
    const pending = candidates.filter((block) => {
      const signature = JSON.stringify([
        block.translation, block.font_size, block.alignment, block.line_spacing,
        block.layout_segments?.map(({ x, y, width, height }) => [x, y, width, height]),
      ]);
      if (previewSignaturesRef.current[block.id] === signature) return false;
      previewSignaturesRef.current[block.id] = signature;
      return true;
    });
    if (!pending.length) return;

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const updates = await Promise.all(pending.map(async (block) => {
          const response = await fetch(apiUrl(`/api/result/${image.folder}/layout-preview`), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            signal: controller.signal,
            body: JSON.stringify({
              translation: block.translation,
              group_id: block.id,
              segments: block.layout_segments,
              font_size: block.font_size,
              minimum_font_size: 8,
              alignment: block.alignment,
              line_spacing: block.line_spacing,
              target_lang: "ENG",
            }),
          });
          if (!response.ok) throw new Error(`Layout preview returned ${response.status}`);
          return { id: block.id, ...(await response.json()) };
        }));
        setLayoutPreviewError(null);
        setBlocks((current) => {
          const next = current.map((block) => {
            const update = updates.find((item) => item.id === block.id);
            if (!update) return block;
            if (!update.fits) {
              return { ...block, review_required: true, review_reason: "text_does_not_fit" };
            }
            return {
              ...block,
              font_size: update.font_size,
              layout_segments: update.layout_segments,
              review_required: update.needs_review === true,
              review_reason: update.needs_review === true ? "uncertain_boundary" : null,
            };
          });
          blocksRef.current = next;
          return next;
        });
      } catch (error) {
        if (!controller.signal.aborted) {
          for (const block of pending) delete previewSignaturesRef.current[block.id];
          setLayoutPreviewError(error instanceof Error ? error.message : "Layout preview failed");
        }
      }
    }, 300);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [blocks, image.folder, layoutPreviewRetry, loading]);

  const pushHistoryCheckpoint = useCallback(() => {
    setPast((history) => [...history.slice(-49), blocksRef.current]);
    setFuture([]);
  }, []);

  const undo = useCallback(() => {
    setPast((history) => {
      const previous = history[history.length - 1];
      if (!previous) return history;
      const current = blocksRef.current;
      blocksRef.current = previous;
      setFuture((redo) => [current, ...redo].slice(0, 50));
      setBlocks(previous);
      setIsDirty(true);
      return history.slice(0, -1);
    });
  }, []);

  const redo = useCallback(() => {
    setFuture((history) => {
      const next = history[0];
      if (!next) return history;
      const current = blocksRef.current;
      blocksRef.current = next;
      setPast((undoHistory) => [...undoHistory.slice(-49), current]);
      setBlocks(next);
      setIsDirty(true);
      return history.slice(1);
    });
  }, []);

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

  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!isDirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isDirty]);

  const handleClose = () => {
    if (isDirty && !window.confirm("You have unsaved editor changes. Close anyway?")) return;
    onClose();
  };

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") handleClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isDirty]);

  const fitPage = useCallback(() => {
    const container = canvasContainerRef.current;
    if (!container) return;
    setZoom(getFitZoom(container.clientWidth, container.clientHeight, imageSize.width, imageSize.height));
  }, [imageSize]);

  useEffect(() => {
    if (!loading) fitPage();
  }, [fitPage, loading]);

  const handleFitText = () => {
    if (!selectedBlock?.translation.trim()) return;

    const fontSize = estimateFitFontSize(
      selectedBlock.translation,
      selectedBlock.width,
      selectedBlock.height,
      selectedBlock.font_size,
      selectedBlock.line_spacing,
    );
    if (fontSize !== selectedBlock.font_size) updateSelectedBlock({ font_size: fontSize });
  };

  const handleFitAllBubbles = () => {
    commitBlocks((prev) =>
      prev.map((b) => {
        const fittedSize = estimateFitFontSize(
          b.translation,
          b.width,
          b.height,
          b.font_size,
          b.line_spacing,
        );
        return { ...b, font_size: fittedSize };
      }),
    );
  };


  // Update selected block helper
  const updateSelectedBlock = (updates: Partial<EditableTextBlock>) => {
    if (!selectedId) return;
    commitBlocks(
      (prev) => prev.map((b) => (b.id === selectedId ? { ...b, ...updates } : b)),
      !isDraggingRef.current,
    );
  };

  // Add new bubble
  const handleAddBubble = () => {
    const newId = `bubble_${Date.now()}`;
    const newBlock: EditableTextBlock = {
      id: newId,
      x: Math.round(imageSize.width / 2 - 120),
      y: Math.round(imageSize.height / 3),
      width: 240,
      height: 130,
      cover_background: false,
      translation: "NEW DIALOGUE",
      original_text: "",
      font_size: 24,
      font_family: "'Comic Neue', cursive, sans-serif",
      fg_color: [0, 0, 0],
      bg_color: [255, 255, 255],
      stroke_width: 3.0,
      angle: 0,
      direction: "h",
      alignment: "center",
      line_spacing: 1.15,
      letter_spacing: 0,
      bold: false,
      italic: false,
    };
    commitBlocks((prev) => [...prev, newBlock]);
    setSelectedId(newId);
  };

  // Delete bubble
  const handleDeleteBubble = (id: string) => {
    commitBlocks((prev) => prev.filter((b) => b.id !== id));
    if (selectedId === id) {
      setSelectedId(null);
    }
  };

  // Duplicate bubble
  const handleDuplicateBubble = () => {
    if (!selectedBlock) return;
    const newId = `bubble_${Date.now()}`;
    const newBlock: EditableTextBlock = {
      ...selectedBlock,
      id: newId,
      x: selectedBlock.x + 30,
      y: selectedBlock.y + 30,
      layout_bounds: undefined,
    };
    commitBlocks((prev) => [...prev, newBlock]);
    setSelectedId(newId);
  };

  // Drag & Resize Handlers
  const handlePointerDown = (e: React.PointerEvent, block: EditableTextBlock, handle?: string, segmentIndex?: number) => {
    e.stopPropagation();
    setSelectedId(block.id);
    pushHistoryCheckpoint();
    isDraggingRef.current = true;

    setDragState({
      type: handle ? "resize" : "move",
      handle,
      startX: e.clientX,
      startY: e.clientY,
      initialBlock: { ...block },
      segmentIndex,
    });
  };

  const handlePointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!dragState.type || !dragState.initialBlock) return;

      const deltaX = (e.clientX - dragState.startX) / zoom;
      const deltaY = (e.clientY - dragState.startY) / zoom;
      const init = dragState.initialBlock;
      const applyGeometry = (geometry: Partial<EditableTextBlock>) => {
        if (dragState.segmentIndex === undefined) {
          updateSelectedBlock(geometry);
          return;
        }
        commitBlocks((previous) => previous.map((owner) => {
          if (owner.id !== selectedId || !owner.layout_segments) return owner;
          return {
            ...owner,
            layout_segments: owner.layout_segments.map((segment, index) =>
              index === dragState.segmentIndex ? { ...segment, ...geometry, rendered_png: null } : segment),
          };
        }), false);
      };

      if (dragState.type === "move") {
        const nextX = Math.round(init.x + deltaX);
        const nextY = Math.round(init.y + deltaY);
        applyGeometry({ x: nextX, y: nextY, layout_bounds: undefined });
      } else if (dragState.type === "resize") {
        let newX = init.x;
        let newY = init.y;
        let newW = init.width;
        let newH = init.height;

        switch (dragState.handle) {
          case "se": // bottom-right
            newW = Math.max(30, init.width + deltaX);
            newH = Math.max(20, init.height + deltaY);
            break;
          case "sw": // bottom-left
            newW = Math.max(30, init.width - deltaX);
            newX = init.x + (init.width - newW);
            newH = Math.max(20, init.height + deltaY);
            break;
          case "ne": // top-right
            newW = Math.max(30, init.width + deltaX);
            newH = Math.max(20, init.height - deltaY);
            newY = init.y + (init.height - newH);
            break;
          case "nw": // top-left
            newW = Math.max(30, init.width - deltaX);
            newX = init.x + (init.width - newW);
            newH = Math.max(20, init.height - deltaY);
            newY = init.y + (init.height - newH);
            break;
          case "e": // right
            newW = Math.max(30, init.width + deltaX);
            break;
          case "s": // bottom
            newH = Math.max(20, init.height + deltaY);
            break;
          case "w": // left
            newW = Math.max(30, init.width - deltaX);
            newX = init.x + (init.width - newW);
            break;
          case "n": // top
            newH = Math.max(20, init.height - deltaY);
            newY = init.y + (init.height - newH);
            break;
        }

        applyGeometry({
          x: Math.round(newX),
          y: Math.round(newY),
          width: Math.round(newW),
          height: Math.round(newH),
          layout_bounds: undefined,
        });
      }
    },
    [commitBlocks, dragState, selectedId, zoom]
  );

  const handlePointerUp = () => {
    isDraggingRef.current = false;
    setDragState({ type: null, startX: 0, startY: 0, initialBlock: null, segmentIndex: undefined });
  };

  // High-Resolution Composite Render to Canvas
  const renderCompositeCanvas = async (): Promise<HTMLCanvasElement> => {
    const canvas = document.createElement("canvas");
    canvas.width = imageSize.width;
    canvas.height = imageSize.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Could not get 2D context");

    // 1. Draw the already decoded background. Never export a silent blank page.
    const background = bgImgRef.current;
    if (!background || !background.complete || !background.naturalWidth) {
      throw new Error("Page artwork is not loaded.");
    }
    ctx.drawImage(background, 0, 0, imageSize.width, imageSize.height);

    // 2. Render all text segments. Linked lobes share style but own geometry/text.
    const renderBlocks = blocks.flatMap((block) =>
      block.layout_segments?.length
        ? block.layout_segments.map((segment) => ({ ...block, ...segment, translation: segment.text, font_size: segment.font_size ?? block.font_size, layout_segments: undefined, linkedLobe: true }))
        : [{ ...block, linkedLobe: false }],
    );
    for (const block of renderBlocks) {
      if (block.linkedLobe && !block.rendered_png) continue;
      if (!block.translation || !block.translation.trim()) continue;

      ctx.save();

      const centerX = block.x + block.width / 2;
      const centerY = block.y + block.height / 2;

      ctx.translate(centerX, centerY);
      if (block.angle) {
        ctx.rotate((block.angle * Math.PI) / 180);
      }
      ctx.translate(-centerX, -centerY);
      ctx.beginPath();
      ctx.rect(block.x, block.y, block.width, block.height);
      ctx.clip();
      if (block.cover_background) {
        ctx.fillStyle = "#fff";
        ctx.fillRect(block.x, block.y, block.width, block.height);
      }

      if (block.rendered_png) {
        const lettering = new Image();
        lettering.src = `data:image/png;base64,${block.rendered_png}`;
        await lettering.decode();
        ctx.drawImage(lettering, block.x, block.y, block.width, block.height);
        ctx.restore();
        continue;
      }

      const fontSize = block.font_size || 24;
      const fontFamily = block.font_family || "Comic Neue, cursive, sans-serif";
      const fontStyle = `${block.italic ? "italic " : ""}${block.bold ? "bold " : ""}${fontSize}px ${fontFamily}`;
      ctx.font = fontStyle;
      ctx.textBaseline = "top";
      ctx.textAlign = block.alignment || "center";

      const textColor = rgbToHex(block.fg_color);
      const strokeColor = rgbToHex(block.bg_color);
      const strokeWidth = block.stroke_width || 0;
      const letterSpacing = block.letter_spacing || 0;
      const measureTextWidth = (value: string) =>
        ctx.measureText(value).width + Math.max(0, Array.from(value).length - 1) * letterSpacing;
      const paintText = (value: string, x: number, y: number, alignment: CanvasTextAlign) => {
        if (!value) return;
        const chars = Array.from(value);
        if (letterSpacing === 0 || chars.length === 1) {
          ctx.textAlign = alignment;
          if (strokeWidth > 0) ctx.strokeText(value, x, y);
          ctx.fillText(value, x, y);
          return;
        }

        const width = measureTextWidth(value);
        let cursor = alignment === "center" ? x - width / 2 : alignment === "right" ? x - width : x;
        ctx.textAlign = "left";
        for (const char of chars) {
          if (strokeWidth > 0) ctx.strokeText(char, cursor, y);
          ctx.fillText(char, cursor, y);
          cursor += ctx.measureText(char).width + letterSpacing;
        }
      };

      const lineHeight = fontSize * (block.line_spacing ?? 1.15);
      let alignX = block.x + block.width / 2;
      if (block.alignment === "left") alignX = block.x + 8;
      if (block.alignment === "right") alignX = block.x + block.width - 8;

      if (strokeWidth > 0) {
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = strokeWidth * 2;
        ctx.lineJoin = "round";
        ctx.miterLimit = 2;
      }
      ctx.fillStyle = textColor;

      if (block.direction === "v") {
        const columns: string[] = [];
        const maxHeight = Math.max(fontSize, block.height - 10);
        for (const rawColumn of block.translation.replace(/\r\n?/g, "\n").split("\n")) {
          let column = "";
          for (const char of Array.from(rawColumn)) {
            if (column && (column.length + 1) * lineHeight - letterSpacing > maxHeight) {
              columns.push(column);
              column = "";
            }
            column += char;
          }
          columns.push(column);
        }

        let columnX = block.x + block.width - fontSize / 2 - 4;
        const startY = block.y + 5;
        for (const column of columns) {
          let y = startY;
          for (const char of Array.from(column)) {
            paintText(char, columnX, y, "center");
            y += lineHeight + letterSpacing;
          }
          columnX -= lineHeight;
        }
      } else {
        if (block.positioned_lines?.length) {
          for (const line of block.positioned_lines) paintText(line.text, line.x, line.y, "left");
        } else {
          const lines = getHorizontalLines(block);
          const totalTextHeight = lines.length * lineHeight;
          let startY = block.y + Math.max(0, (block.height - totalTextHeight) / 2);
          for (const line of lines) {
            paintText(line, alignX, startY, block.alignment || "center");
            startY += lineHeight;
          }
        }
      }

      ctx.restore();
    }

    return canvas;
  };

  // Export High-Res PNG
  const handleExportPng = async () => {
    try {
      const canvas = await renderCompositeCanvas();
      canvas.toBlob((blob) => {
        if (!blob) return;
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        const nameWithoutExt = image.originalName.replace(/\.[^/.]+$/, "");
        a.download = `${nameWithoutExt}_typeset.png`;
        a.click();
        URL.revokeObjectURL(url);
      }, "image/png");
    } catch (err) {
      console.error("Failed to export composite image:", err);
      alert("Failed to export image. Check console for details.");
    }
  };

  // Save Edits to Studio / Server
  const handleSaveToStudio = async () => {
    if (!image.folder) {
      alert("Image does not have an active result folder on the server.");
      return;
    }
    setSaving(true);
    try {
      const canvas = await renderCompositeCanvas();
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!blob) {
        throw new Error("Failed to encode canvas image.");
      }

      const base64Image = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => {
          if (typeof reader.result === "string") {
            resolve(reader.result);
          } else {
            reject(new Error("Failed to convert image to data URL"));
          }
        };
        reader.onerror = () => reject(reader.error || new Error("Failed to read image blob"));
        reader.readAsDataURL(blob);
      });

      const response = await fetch(apiUrl(`/api/result/${image.folder}/save_edits`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text_regions: blocks,
          final_image_base64: base64Image,
        }),
      });

      if (!response.ok) {
        throw new Error(`Server returned ${response.status}`);
      }
      const saved = await response.json().catch(() => ({}));
      setIsDirty(false);
      setPast([]);
      setFuture([]);

      if (onSave) {
        onSave({
          ...image,
          result: blob,
          hasTextRegions: true,
          reviewStatus: saved.reviewStatus,
          reviewedAt: saved.reviewedAt ?? null,
        });
      }
    } catch (err) {
      console.error("Save failed:", err);
      alert(`Save failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(false);
    }
  };

  // Export Project JSON
  const handleExportJson = () => {
    const jsonStr = JSON.stringify(blocks, null, 2);
    const blob = new Blob([jsonStr], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${image.originalName}_typesetting.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const pendingReviewCount = blocks.filter((block) => block.review_required).length;
  const pendingReviewBlock = blocks.find((block) => block.review_required) || null;
  const pendingReviewReason = getReviewReasonCopy(
    (selectedBlock?.review_required ? selectedBlock : pendingReviewBlock)?.review_reason,
  );

  return (
    <div
      className="fixed inset-0 z-50 flex w-screen max-w-[100vw] min-w-0 flex-col overflow-hidden bg-zinc-950/90 text-zinc-100 backdrop-blur-md"
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
    >
      {/* Top Header Bar */}
      <header className="flex min-w-0 flex-wrap items-center justify-between gap-2 overflow-hidden border-b border-zinc-800 bg-zinc-900/90 px-4 py-3 select-none sm:px-6">
        <div className="flex min-w-0 items-center space-x-3">
          <div className="p-2 bg-indigo-500/20 text-indigo-400 rounded-lg">
            <Icon icon="carbon:text-annotation-toggle" className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-bold tracking-tight text-white flex items-center space-x-2">
              <span>Manga Typesetter & Visual Editor</span>
              <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded-full bg-indigo-900/60 text-indigo-300 border border-indigo-700/50">
                Interactive
              </span>
            </h2>
            <p className="text-xs text-zinc-400 truncate max-w-md">{image.originalName}</p>
          </div>
        </div>

        {/* Zoom & View Controls */}
        <div className="flex shrink-0 items-center space-x-2 rounded-lg border border-zinc-700/60 bg-zinc-800/80 px-2 py-1 text-xs">
          <button
            type="button"
            onClick={() => setZoom((z) => Math.max(0.05, z - 0.15))}
            className="p-1 hover:bg-zinc-700 rounded text-zinc-300"
            title="Zoom Out"
          >
            <Icon icon="carbon:zoom-out" className="w-4 h-4" />
          </button>
          <span className="font-mono w-12 text-center text-zinc-200">
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            onClick={() => setZoom((z) => Math.min(3, z + 0.15))}
            className="p-1 hover:bg-zinc-700 rounded text-zinc-300"
            title="Zoom In"
          >
            <Icon icon="carbon:zoom-in" className="w-4 h-4" />
          </button>
          <button
            type="button"
            onClick={() => setZoom(1)}
            className="p-1 hover:bg-zinc-700 rounded text-zinc-300 ml-1"
            title="Reset Zoom"
          >
            <Icon icon="carbon:zoom-reset" className="w-4 h-4" />
          </button>
          <button
            type="button"
            onClick={fitPage}
            className="px-2 py-1 hover:bg-zinc-700 rounded text-zinc-300"
            title="Fit page to available canvas"
          >
            Fit
          </button>
          <div className="w-px h-4 bg-zinc-700 mx-1" />
          <button
            type="button"
            onClick={() => setShowOriginalOverlay((v) => !v)}
            className={`px-2 py-1 rounded flex items-center space-x-1 ${
              showOriginalOverlay ? "bg-amber-950 text-amber-100 border border-amber-500/40" : "hover:bg-zinc-700 text-white"
            }`}
            title="Toggle Original Raw Image for Reference"
          >
            <Icon icon="carbon:compare" className="w-3.5 h-3.5" />
            <span>Raw Art</span>
          </button>
        </div>

        {/* Actions Toolbar */}
        <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            onClick={handleAddBubble}
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg text-xs font-medium border border-zinc-700 transition-colors"
          >
            <Icon icon="carbon:add-alt" className="w-4 h-4 text-indigo-400" />
            <span>Add Bubble</span>
          </button>

          <button
            type="button"
            onClick={handleFitAllBubbles}
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg text-xs font-medium border border-zinc-700 transition-colors"
            title="Auto-fit font size for all speech bubbles to avoid overflow"
          >
            <Icon icon="carbon:fit-to-screen" className="w-4 h-4 text-cyan-400" />
            <span>Auto-fit All</span>
          </button>

          <div className="flex items-center rounded-lg border border-zinc-700 bg-zinc-800">
            <button
              type="button"
              onClick={undo}
              disabled={!past.length}
              className="px-2 py-1.5 text-zinc-300 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40"
              title="Undo (⌘/Ctrl+Z)"
            >
              <Icon icon="carbon:undo" className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={redo}
              disabled={!future.length}
              className="px-2 py-1.5 text-zinc-300 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40"
              title="Redo (⌘/Ctrl+Shift+Z)"
            >
              <Icon icon="carbon:redo" className="w-4 h-4" />
            </button>
          </div>


          <button
            type="button"
            onClick={handleExportPng}
            disabled={Boolean(backgroundError)}
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg text-xs font-medium border border-zinc-700 transition-colors"
            title="Download full resolution composited PNG"
          >
            <Icon icon="carbon:download" className="w-4 h-4 text-emerald-400" />
            <span>Export PNG</span>
          </button>

          <button
            type="button"
            onClick={handleExportJson}
            className="p-2 hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 rounded-lg text-xs"
            title="Download project JSON"
          >
            <Icon icon="carbon:document-export" className="w-4 h-4" />
          </button>

          <button
            type="button"
            onClick={handleClose}
            className="p-2 hover:bg-zinc-800 text-zinc-400 hover:text-white rounded-lg transition-colors ml-2"
            title="Close editor"
          >
            <Icon icon="carbon:close" className="w-5 h-5" />
          </button>
        </div>
      </header>

      {/* Main Workspace: Left Canvas + Right Inspector */}
      <div className="grid min-h-0 min-w-0 w-full flex-1 grid-cols-1 overflow-hidden md:grid-cols-[minmax(0,1fr)_minmax(18rem,20rem)]">
        {/* Central Visual Canvas Area */}
        <div
          ref={canvasContainerRef}
          className="min-h-0 min-w-0 flex-1 overflow-auto bg-zinc-950 flex items-start justify-start p-8 relative"
          onClick={() => setSelectedId(null)}
        >
          {loading ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center space-y-3 text-zinc-400">
              <Icon icon="carbon:circle-dash" className="w-8 h-8 animate-spin text-indigo-500" />
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
                    src={showOriginalOverlay && typeof image.inputUrl === "string" ? apiUrl(image.inputUrl) : bgImageUrl}
                    alt="Manga Canvas"
                    className="w-full h-full object-contain pointer-events-none"
                    draggable={false}
                  />
                ) : (
                  <div className="absolute inset-0 flex items-center justify-center bg-zinc-900 text-center text-zinc-400 p-8">
                    <div>
                      <Icon icon="carbon:image-search" className="w-10 h-10 mx-auto mb-3 text-amber-400" />
                      <p className="text-sm font-medium text-zinc-200">Page artwork unavailable</p>
                      <p className="text-xs mt-1">Text editing is available, but export is disabled until an image can be loaded.</p>
                    </div>
                  </div>
                )}

                {/* Interactive Speech Bubbles / Text Layer */}
                {!showOriginalOverlay && visualBlocks.map(({ owner, block, segmentIndex }) => {
                const isSelected = block.id === selectedId;
                const textColor = rgbToHex(block.fg_color);
                const strokeColor = rgbToHex(block.bg_color);
                const strokePx =
                  typeof block.stroke_width === "number" && block.stroke_width > 0
                    ? Math.max(1.5, block.stroke_width >= 1 ? block.stroke_width : Math.round(block.font_size * 0.08))
                    : 0;

                  return (
                    <div
                      key={`${block.id}:${segmentIndex ?? "single"}`}
                      onPointerDown={(e) => handlePointerDown(e, block, undefined, segmentIndex)}
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
                        transform: block.angle ? `rotate(${block.angle}deg)` : undefined,
                        transformOrigin: "center center",
                      }}
                    >
                    {block.cover_background && (
                      <div className="absolute inset-0 bg-white pointer-events-none" aria-hidden="true" />
                    )}
                    {block.review_required && (segmentIndex === undefined || segmentIndex === 0) && <span className="absolute -top-6 left-0 whitespace-nowrap rounded bg-amber-950 px-2 py-1 text-xs text-amber-100">Needs editing</span>}
                    {!(segmentIndex !== undefined && owner.layout_segments?.length && !block.rendered_png) && (
                      <div
                        className={`relative w-full h-full overflow-hidden pointer-events-none ${block.positioned_lines?.length ? "" : "flex flex-col justify-center px-2 py-1"}`}
                        style={{
                          fontFamily: block.font_family || "'Comic Neue', cursive, sans-serif",
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
                          writingMode: block.direction === "v" ? "vertical-rl" : "horizontal-tb",
                        }}
                      >
                        {block.rendered_png
                          ? <img src={`data:image/png;base64,${block.rendered_png}`} alt="" className="absolute inset-0 w-full h-full" />
                          : block.positioned_lines?.length
                          ? block.positioned_lines.map((line, index) => (
                              <span key={index} className="absolute whitespace-nowrap select-none"
                                style={{ left: line.x - block.x, top: line.y - block.y }}>
                                {line.text}
                              </span>
                            ))
                          : <span className="whitespace-pre-wrap [word-break:normal] [overflow-wrap:normal] select-none">
                              {block.translation
                                ? block.direction === "h"
                                  ? getHorizontalLines(block).join("\n")
                                  : block.translation
                                : "..."}
                            </span>}
                      </div>
                    )}


                    {/* Resize Handles (Only shown when selected) */}
                    {isSelected && (
                      <>
                        {/* 8 Resize Points */}
                        <div
                          onPointerDown={(e) => handlePointerDown(e, block, "nw", segmentIndex)}
                          className="absolute -top-1.5 -left-1.5 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-nwse-resize z-30"
                        />
                        <div
                          onPointerDown={(e) => handlePointerDown(e, block, "n", segmentIndex)}
                          className="absolute -top-1.5 left-1/2 -translate-x-1/2 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-ns-resize z-30"
                        />
                        <div
                          onPointerDown={(e) => handlePointerDown(e, block, "ne", segmentIndex)}
                          className="absolute -top-1.5 -right-1.5 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-nesw-resize z-30"
                        />
                        <div
                          onPointerDown={(e) => handlePointerDown(e, block, "e", segmentIndex)}
                          className="absolute top-1/2 -right-1.5 -translate-y-1/2 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-ew-resize z-30"
                        />
                        <div
                          onPointerDown={(e) => handlePointerDown(e, block, "se", segmentIndex)}
                          className="absolute -bottom-1.5 -right-1.5 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-nwse-resize z-30"
                        />
                        <div
                          onPointerDown={(e) => handlePointerDown(e, block, "s", segmentIndex)}
                          className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-ns-resize z-30"
                        />
                        <div
                          onPointerDown={(e) => handlePointerDown(e, block, "sw", segmentIndex)}
                          className="absolute -bottom-1.5 -left-1.5 w-3 h-3 bg-white border-2 border-indigo-600 rounded-xs cursor-nesw-resize z-30"
                        />
                        <div
                          onPointerDown={(e) => handlePointerDown(e, block, "w", segmentIndex)}
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
            <div role="alert" className="absolute bottom-3 left-3 right-3 z-40 flex items-center justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-950/90 px-3 py-2 text-xs text-amber-100">
              <span>Linked bubble preview could not refresh. The last valid placement is still shown.</span>
              <button type="button" className="shrink-0 rounded-md border border-amber-500/60 px-3 py-1.5 font-semibold text-white hover:bg-amber-900" onClick={() => setLayoutPreviewRetry((value) => value + 1)}>Retry</button>
            </div>
          )}
        </div>

        {/* Right Sidebar: Inspector Panel */}
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
                {selectedBlock.review_required && (() => {
                  const reason = getReviewReasonCopy(selectedBlock.review_reason);
                  return (
                  <div role="status" className="rounded-lg border border-amber-700 bg-amber-950/40 p-3 text-sm text-amber-100">
                    <p className="font-semibold">Review reason: {reason.label}</p>
                    <p className="mt-2 text-amber-100/90">{reason.guidance}</p>
                    <div className="mt-3 grid gap-2">
                      <button type="button" className="w-full rounded border border-amber-600 px-3 py-2 text-left font-semibold hover:bg-amber-900/50" onClick={() => updateSelectedBlock({ review_required: false, review_reason: null, cover_background: true })}>Replace original with this translation</button>
                      <button type="button" className="w-full rounded border border-zinc-600 px-3 py-2 text-left text-zinc-200 hover:bg-zinc-800" onClick={() => updateSelectedBlock({ translation: "", review_required: false, review_reason: null, cover_background: false })}>Accept preserved original</button>
                    </div>
                  </div>
                  );
                })()}
                {/* TAB 1: TEXT CONTENT */}
                {activeTab === "text" && (
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
                          onChange={(e) => updateSelectedBlock({ cover_background: e.target.checked })}
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
                            updateSelectedBlock({ translation: selectedBlock.translation.toUpperCase() })
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
                            updateSelectedBlock({ translation: selectedBlock.translation.toLowerCase() })
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
                )}

                {/* TAB 2: STYLE, FONTS, COLORS & STROKES */}
                {activeTab === "style" && (
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
                        <span className="font-mono text-zinc-400">{selectedBlock.font_size} px</span>
                      </div>
                      <input
                        type="range"
                        min="10"
                        max="90"
                        step="1"
                        value={selectedBlock.font_size}
                        onChange={(e) => updateSelectedBlock({ font_size: Number(e.target.value) })}
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
                          onChange={(e) => updateSelectedBlock({ fg_color: hexToRgb(e.target.value) })}
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
                          <Icon icon="carbon:contour-finding" className="w-4 h-4 text-indigo-400" />
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
                        onChange={(e) => updateSelectedBlock({ stroke_width: Number(e.target.value) })}
                        className="w-full accent-indigo-500 cursor-pointer"
                      />

                      <div className="flex items-center space-x-2">
                        <input
                          type="color"
                          value={rgbToHex(selectedBlock.bg_color)}
                          onChange={(e) => updateSelectedBlock({ bg_color: hexToRgb(e.target.value) })}
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
                          onClick={() => updateSelectedBlock({ italic: !selectedBlock.italic })}
                          className={`py-1.5 px-3 rounded flex items-center justify-center space-x-1 text-xs italic border ${
                            selectedBlock.italic
                              ? "bg-indigo-600 border-indigo-500 text-white"
                              : "bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700"
                          }`}
                          title="Italic (Standard for Thoughts/Whispers)"
                        >
                          <span>I</span>
                          <span className="text-[10px] font-normal not-italic">Italic (Thoughts)</span>
                        </button>
                      </div>
                    </div>
                  </div>
                )}

                {/* TAB 3: LAYOUT, SPACING & ROTATION */}
                {activeTab === "layout" && (
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
                        onChange={(e) => updateSelectedBlock({ line_spacing: Number(e.target.value) })}
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
                        onChange={(e) => updateSelectedBlock({ letter_spacing: Number(e.target.value) })}
                        className="w-full accent-indigo-500 cursor-pointer"
                      />
                    </div>

                    {/* Rotation Angle */}
                    <div>
                      <div className="flex justify-between items-center text-xs text-zinc-300 mb-1">
                        <span className="font-semibold">Rotation Angle</span>
                        <span className="font-mono text-zinc-400">{selectedBlock.angle || 0}°</span>
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
                  Click any speech bubble on the canvas to edit its text, font, color, size, stroke, and position.
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
      </div>

      <footer className="flex shrink-0 flex-col items-stretch gap-2 border-t border-zinc-800 bg-zinc-900/95 px-4 py-3 sm:px-6">
        {pendingReviewCount > 0 && (
          <div role="status" className="min-w-0 flex-1 text-xs text-amber-200">
            <p className="truncate font-semibold">
              {pendingReviewCount} bubble{pendingReviewCount === 1 ? "" : "s"} need review · {pendingReviewReason.label}
            </p>
            <p className="truncate text-amber-100/75">{pendingReviewReason.guidance}</p>
          </div>
        )}
        <div className="flex w-full min-w-0 flex-wrap items-center justify-start gap-2">
          {selectedBlock?.review_required && (
            <>
              <button
                type="button"
                className="rounded border border-amber-600 px-3 py-2 text-xs font-semibold text-amber-100 hover:bg-amber-900/50"
                onClick={() => updateSelectedBlock({ review_required: false, review_reason: null, cover_background: true })}
              >
                Replace original
              </button>
              <button
                type="button"
                className="rounded border border-zinc-600 px-3 py-2 text-xs text-zinc-200 hover:bg-zinc-800"
                onClick={() => updateSelectedBlock({ translation: "", review_required: false, review_reason: null, cover_background: false })}
              >
                Keep original
              </button>
            </>
          )}
          {pendingReviewCount > 0 && (
            <button
              type="button"
              onClick={handleSaveToStudio}
              disabled={saving || Boolean(backgroundError)}
              className="flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-3.5 py-2 text-xs font-semibold text-zinc-200 transition-all hover:bg-zinc-700 disabled:bg-zinc-900 disabled:text-zinc-500"
              title="Save the current edits without approving the page"
            >
              <Icon icon={saving ? "carbon:circle-dash" : "carbon:save"} className={`h-4 w-4 ${saving ? "animate-spin" : ""}`} />
              <span>{saving ? "Saving..." : "Save draft"}</span>
            </button>
          )}
          <button
            type="button"
            onClick={handleSaveToStudio}
            disabled={saving || Boolean(backgroundError) || pendingReviewCount > 0}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white shadow-md transition-all hover:bg-indigo-500 disabled:bg-indigo-900 disabled:text-indigo-300"
            title={pendingReviewCount > 0 ? `Resolve ${pendingReviewCount} flagged bubble${pendingReviewCount === 1 ? "" : "s"} before approving` : "Save edits and approve this page"}
          >
            <Icon icon={saving ? "carbon:circle-dash" : "carbon:save"} className={`h-4 w-4 ${saving ? "animate-spin" : ""}`} />
            <span>{saving ? "Saving..." : "Save & approve page"}</span>
          </button>
        </div>
      </footer>
    </div>
  );
};
