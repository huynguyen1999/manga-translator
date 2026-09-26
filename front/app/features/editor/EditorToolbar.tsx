import React from "react";
import { Icon } from "@iconify/react";

interface EditorToolbarProps {
  imageName: string;
  zoom: number;
  setZoom: React.Dispatch<React.SetStateAction<number>>;
  fitPage: () => void;
  showOriginalOverlay: boolean;
  setShowOriginalOverlay: React.Dispatch<React.SetStateAction<boolean>>;
  handleAddBubble: () => void;
  handleFitAllBubbles: () => void;
  undo: () => void;
  redo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  handleExportPng: () => void;
  hasBackgroundError: boolean;
  handleExportJson: () => void;
  handleClose: () => void;
}

export const EditorToolbar: React.FC<EditorToolbarProps> = ({
  imageName,
  zoom,
  setZoom,
  fitPage,
  showOriginalOverlay,
  setShowOriginalOverlay,
  handleAddBubble,
  handleFitAllBubbles,
  undo,
  redo,
  canUndo,
  canRedo,
  handleExportPng,
  hasBackgroundError,
  handleExportJson,
  handleClose,
}) => (
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
        <p className="text-xs text-zinc-400 truncate max-w-md">{imageName}</p>
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
          showOriginalOverlay
            ? "bg-amber-950 text-amber-100 border border-amber-500/40"
            : "hover:bg-zinc-700 text-white"
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
          disabled={!canUndo}
          className="px-2 py-1.5 text-zinc-300 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40"
          title="Undo (⌘/Ctrl+Z)"
        >
          <Icon icon="carbon:undo" className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={redo}
          disabled={!canRedo}
          className="px-2 py-1.5 text-zinc-300 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40"
          title="Redo (⌘/Ctrl+Shift+Z)"
        >
          <Icon icon="carbon:redo" className="w-4 h-4" />
        </button>
      </div>

      <button
        type="button"
        onClick={handleExportPng}
        disabled={hasBackgroundError}
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
);
