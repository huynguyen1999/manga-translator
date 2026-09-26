import React from 'react';
import { Icon } from '@iconify/react';
import { getReaderTitle } from '@/features/reader/PageItem';
import type { ReaderMode, ReaderWidth, SinglePageFit } from '@/features/reader/PageItem';

const widthPresets: { label: string; value: ReaderWidth; desc: string }[] = [
  { label: 'Compact', value: '60%', desc: '60% of screen width' },
  { label: 'Standard', value: '75%', desc: '75% of screen width' },
  { label: 'Default', value: '90%', desc: '90% of screen width (Default)' },
  { label: 'Full Width', value: '100%', desc: '100% of screen width' },
];

interface ReaderWidthMenuProps {
  touchDevice: boolean;
  readerMode: ReaderMode;
  readerWidth: ReaderWidth;
  showWidthMenu: boolean;
  singlePageFit: SinglePageFit;
  onToggle: () => void;
  onClose: () => void;
  onSetWidth: (width: ReaderWidth) => void;
  onSetSinglePageFit: (fit: SinglePageFit) => void;
  onStartOver: () => void;
}

export const ReaderWidthMenu: React.FC<ReaderWidthMenuProps> = ({
  touchDevice,
  readerMode,
  readerWidth,
  showWidthMenu,
  singlePageFit,
  onToggle,
  onClose,
  onSetWidth,
  onSetSinglePageFit,
  onStartOver,
}) => (
  <div className="relative">
    <button
      type="button"
      onClick={onToggle}
      className="min-h-8 sm:min-h-10 flex items-center space-x-1 px-2 sm:px-2.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white text-xs border border-zinc-700/60 transition-colors"
      title={getReaderTitle('Change page width', touchDevice)}
      aria-label={`Change page width, currently ${readerWidth}`}
    >
      <Icon icon="carbon:fit-to-width" className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-indigo-400" />
      <span className="font-mono text-xs hidden md:inline">{readerWidth}</span>
      <Icon icon="carbon:chevron-down" className="w-3 h-3 text-zinc-400 hidden sm:inline" />
    </button>

    {showWidthMenu && (
      <div
        className="absolute right-0 mt-1.5 w-44 rounded-xl bg-zinc-900 border border-zinc-700 shadow-2xl p-1 z-50 animate-in fade-in zoom-in-95"
        onMouseLeave={onClose}
      >
        <div className="px-2.5 py-1.5 text-xs font-semibold text-zinc-300 uppercase tracking-wider border-b border-zinc-800">
          Page Width
        </div>
        {widthPresets.map((preset) => (
          <button
            key={preset.value}
            type="button"
            onClick={() => onSetWidth(preset.value)}
            className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs transition-colors ${
              readerWidth === preset.value
                ? 'bg-indigo-600/30 text-indigo-300 font-semibold'
                : 'text-zinc-300 hover:bg-zinc-800 hover:text-white'
            }`}
          >
            <span>{preset.label}</span>
            <span className="text-xs font-mono text-zinc-400">{preset.value}</span>
          </button>
        ))}

        {readerMode === 'single' && (
          <>
            <div className="my-1 border-t border-zinc-800" />
            <div className="px-2.5 py-1 text-xs font-semibold text-zinc-300 uppercase tracking-wider">
              Single Page Fit
            </div>
            <button
              type="button"
              onClick={() => onSetSinglePageFit('height')}
              className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs transition-colors ${
                singlePageFit === 'height'
                  ? 'bg-indigo-600/30 text-indigo-300 font-semibold'
                  : 'text-zinc-300 hover:bg-zinc-800 hover:text-white'
              }`}
            >
              <div className="flex items-center space-x-1.5">
                <Icon icon="carbon:fit-to-screen" className="w-3.5 h-3.5" />
                <span>Fit Screen Height</span>
              </div>
            </button>
            <button
              type="button"
              onClick={() => onSetSinglePageFit('width')}
              className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs transition-colors ${
                singlePageFit === 'width'
                  ? 'bg-indigo-600/30 text-indigo-300 font-semibold'
                  : 'text-zinc-300 hover:bg-zinc-800 hover:text-white'
              }`}
            >
              <div className="flex items-center space-x-1.5">
                <Icon icon="carbon:fit-to-width" className="w-3.5 h-3.5" />
                <span>Fit Page Width</span>
              </div>
            </button>
          </>
        )}
        <div className="my-1 border-t border-zinc-800" />
        <button
          type="button"
          onClick={onStartOver}
          className="w-full flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors"
        >
          <Icon icon="carbon:rewind-10" className="w-3.5 h-3.5" />
          <span>Start over</span>
        </button>
      </div>
    )}
  </div>
);
