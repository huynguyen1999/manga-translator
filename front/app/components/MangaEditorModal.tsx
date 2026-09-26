import React, { useState, useEffect, useRef, useCallback } from "react";
import type { FinishedImage, EditableTextBlock } from "@/types";
import {
  estimateFitFontSize,
  getFitZoom,
} from "@/features/editor/geometry";
import { apiUrl } from "@/utils/api";
import { loadEditorDocument } from "@/features/editor/loadEditorDocument";
import { EditorCanvasStage } from "@/features/editor/EditorCanvasStage";
import { EditorToolbar } from "@/features/editor/EditorToolbar";
import { EditorInspector, getReviewReasonCopy, type EditorTab } from "@/features/editor/EditorInspector";
import { EditorReviewFooter } from "@/features/editor/EditorReviewFooter";
import { useEditorHistory } from "@/features/editor/useEditorHistory";
import { useEditorDrag } from "@/features/editor/useEditorDrag";
import { useEditorExports } from "@/features/editor/useEditorExports";

interface MangaEditorModalProps {
  image: FinishedImage;
  onClose: () => void;
  onSave?: (updatedImage: FinishedImage) => void;
}

export const MangaEditorModal: React.FC<MangaEditorModalProps> = ({
  image,
  onClose,
  onSave,
}) => {
  const [blocks, setBlocks] = useState<EditableTextBlock[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [bgImageUrl, setBgImageUrl] = useState<string | null>(null);
  const [imageSize, setImageSize] = useState<{ width: number; height: number }>(
    { width: 1000, height: 1400 },
  );
  const [loading, setLoading] = useState(true);
  const [backgroundError, setBackgroundError] = useState<string | null>(null);
  const [backgroundNotice, setBackgroundNotice] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [showOriginalOverlay, setShowOriginalOverlay] = useState(false);
  const [activeTab, setActiveTab] = useState<EditorTab>(
    "text",
  );
  const [isDirty, setIsDirty] = useState(false);
  const [layoutPreviewError, setLayoutPreviewError] = useState<string | null>(
    null,
  );
  const [layoutPreviewRetry, setLayoutPreviewRetry] = useState(0);

  const canvasContainerRef = useRef<HTMLDivElement>(null);
  const bgImgRef = useRef<HTMLImageElement | null>(null);
  const blocksRef = useRef<EditableTextBlock[]>([]);
  const previewSignaturesRef = useRef<Record<string, string>>({});

  // Load Background Image and Text Regions
  useEffect(() => {
    let isMounted = true;
    setLoading(true);
    setBgImageUrl(null);
    setBackgroundError(null);
    setBackgroundNotice(null);
    bgImgRef.current = null;

    const cleanupPromise = loadEditorDocument(image).then((document) => {
      if (isMounted) {
        setBgImageUrl(document.background?.url ?? null);
        setImageSize(document.imageSize);
        bgImgRef.current = document.background?.image ?? null;
        setBackgroundError(document.backgroundError);
        setBackgroundNotice(document.backgroundNotice);
        blocksRef.current = document.blocks;
        setBlocks(document.blocks);
        resetHistory();
        setIsDirty(false);
        setSelectedId(
          document.blocks.find((block) => block.review_required)?.id ||
            document.blocks[0]?.id ||
            null,
        );
        setLoading(false);
      }
      return document.cleanup;
    });

    return () => {
      isMounted = false;
      cleanupPromise.then((cleanup) => cleanup?.());
    };
  }, [image]);

  const selectedBlock = blocks.find((b) => b.id === selectedId) || null;
  const visualBlocks = blocks.reduce<
    Array<{
      owner: EditableTextBlock;
      block: EditableTextBlock;
      segmentIndex?: number;
    }>
  >((items, owner) => {
    if (!owner.layout_segments?.length)
      return [...items, { owner, block: owner }];
    return items.concat(
      owner.layout_segments.map((segment, segmentIndex) => ({
        owner,
        block: {
          ...owner,
          ...segment,
          translation: segment.text,
          font_size: segment.font_size ?? owner.font_size,
          layout_segments: undefined,
        } as EditableTextBlock,
        segmentIndex,
      })),
    );
  }, []);

  const {
    past,
    future,
    commitBlocks,
    pushHistoryCheckpoint,
    undo,
    redo,
    resetHistory,
  } = useEditorHistory(blocksRef, setBlocks, setIsDirty);

  const {
    isDraggingRef,
    handlePointerDown,
    handlePointerMove,
    handlePointerUp,
  } = useEditorDrag({
    selectedId,
    setSelectedId,
    zoom,
    commitBlocks,
    pushHistoryCheckpoint,
  });

  const {
    saving,
    handleExportPng,
    handleSaveToStudio,
    handleExportJson,
  } = useEditorExports({
    image,
    blocks,
    bgImageRef: bgImgRef,
    imageSize,
    setIsDirty,
    resetHistory,
    onSave,
  });

  useEffect(() => {
    if (!image.folder || loading) return;
    const candidates = blocks.filter(
      (block) => (block.layout_segments?.length || 0) > 1,
    );
    const pending = candidates.filter((block) => {
      const signature = JSON.stringify([
        block.translation,
        block.font_size,
        block.alignment,
        block.line_spacing,
        block.layout_segments?.map(({ x, y, width, height }) => [
          x,
          y,
          width,
          height,
        ]),
      ]);
      if (previewSignaturesRef.current[block.id] === signature) return false;
      previewSignaturesRef.current[block.id] = signature;
      return true;
    });
    if (!pending.length) return;

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const updates = await Promise.all(
          pending.map(async (block) => {
            const response = await fetch(
              apiUrl(`/api/result/${image.folder}/layout-preview`),
              {
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
              },
            );
            if (!response.ok)
              throw new Error(`Layout preview returned ${response.status}`);
            return { id: block.id, ...(await response.json()) };
          }),
        );
        setLayoutPreviewError(null);
        setBlocks((current) => {
          const next = current.map((block) => {
            const update = updates.find((item) => item.id === block.id);
            if (!update) return block;
            if (!update.fits) {
              return {
                ...block,
                review_required: true,
                review_reason: "text_does_not_fit",
              };
            }
            return {
              ...block,
              font_size: update.font_size,
              layout_segments: update.layout_segments,
              review_required: update.needs_review === true,
              review_reason:
                update.needs_review === true ? "uncertain_boundary" : null,
            };
          });
          blocksRef.current = next;
          return next;
        });
      } catch (error) {
        if (!controller.signal.aborted) {
          for (const block of pending)
            delete previewSignaturesRef.current[block.id];
          setLayoutPreviewError(
            error instanceof Error ? error.message : "Layout preview failed",
          );
        }
      }
    }, 300);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [blocks, image.folder, layoutPreviewRetry, loading]);

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
    if (
      isDirty &&
      !window.confirm("You have unsaved editor changes. Close anyway?")
    )
      return;
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
    setZoom(
      getFitZoom(
        container.clientWidth,
        container.clientHeight,
        imageSize.width,
        imageSize.height,
      ),
    );
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
    if (fontSize !== selectedBlock.font_size)
      updateSelectedBlock({ font_size: fontSize });
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
      (prev) =>
        prev.map((b) => (b.id === selectedId ? { ...b, ...updates } : b)),
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

  const pendingReviewCount = blocks.filter(
    (block) => block.review_required,
  ).length;
  const pendingReviewBlock =
    blocks.find((block) => block.review_required) || null;
  const pendingReviewReason = getReviewReasonCopy(
    (selectedBlock?.review_required ? selectedBlock : pendingReviewBlock)
      ?.review_reason,
  );

  return (
    <div
      className="fixed inset-0 z-50 flex w-screen max-w-[100vw] min-w-0 flex-col overflow-hidden bg-zinc-950/90 text-zinc-100 backdrop-blur-md"
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
    >
      <EditorToolbar
        imageName={image.originalName}
        zoom={zoom}
        setZoom={setZoom}
        fitPage={fitPage}
        showOriginalOverlay={showOriginalOverlay}
        setShowOriginalOverlay={setShowOriginalOverlay}
        handleAddBubble={handleAddBubble}
        handleFitAllBubbles={handleFitAllBubbles}
        undo={undo}
        redo={redo}
        canUndo={Boolean(past.length)}
        canRedo={Boolean(future.length)}
        handleExportPng={handleExportPng}
        hasBackgroundError={Boolean(backgroundError)}
        handleExportJson={handleExportJson}
        handleClose={handleClose}
      />

      {/* Main Workspace: Left Canvas + Right Inspector */}
      <div className="grid min-h-0 min-w-0 w-full flex-1 grid-cols-1 overflow-hidden md:grid-cols-[minmax(0,1fr)_minmax(18rem,20rem)]">
        <EditorCanvasStage
          image={image}
          canvasContainerRef={canvasContainerRef}
          imageSize={imageSize}
          zoom={zoom}
          bgImageUrl={bgImageUrl}
          showOriginalOverlay={showOriginalOverlay}
          loading={loading}
          visualBlocks={visualBlocks}
          selectedId={selectedId}
          handlePointerDown={handlePointerDown}
          handleDeleteBubble={handleDeleteBubble}
          backgroundError={backgroundError}
          backgroundNotice={backgroundNotice}
          layoutPreviewError={layoutPreviewError}
          onClearSelection={() => setSelectedId(null)}
          onRetryLayoutPreview={() => setLayoutPreviewRetry((value) => value + 1)}
        />
        <EditorInspector
          selectedBlock={selectedBlock}
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          updateSelectedBlock={updateSelectedBlock}
          handleFitText={handleFitText}
          handleDuplicateBubble={handleDuplicateBubble}
          handleDeleteBubble={handleDeleteBubble}
          handleAddBubble={handleAddBubble}
        />
      </div>

      <EditorReviewFooter
        pendingReviewCount={pendingReviewCount}
        pendingReviewReason={pendingReviewReason}
        selectedBlock={selectedBlock}
        saving={saving}
        backgroundError={backgroundError}
        updateSelectedBlock={updateSelectedBlock}
        onSave={handleSaveToStudio}
      />
    </div>
  );
};
