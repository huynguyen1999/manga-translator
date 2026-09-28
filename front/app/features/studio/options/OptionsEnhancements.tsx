import React from "react";
import { Icon } from "@iconify/react";
import {
  colorizerOptions,
  colorizationSizes,
  upscalerOptions,
  upscaleRatioOptions,
} from "@/config";

interface OptionsEnhancementsProps {
  colorizer: string;
  setColorizer: (val: string) => void;
  colorizeOnly: boolean;
  setColorizeOnly: (val: boolean) => void;
  colorizationSize: string;
  setColorizationSize: (val: string) => void;
  colorThreshold: number;
  setColorThreshold: (val: number) => void;
  upscaler: string;
  setUpscaler: (val: string) => void;
  upscaleRatio: string;
  setUpscaleRatio: (val: string) => void;
  revertUpscaling: boolean;
  setRevertUpscaling: (val: boolean) => void;
}

export const OptionsEnhancements: React.FC<OptionsEnhancementsProps> = ({
  colorizer,
  setColorizer,
  colorizeOnly,
  setColorizeOnly,
  colorizationSize,
  setColorizationSize,
  colorThreshold,
  setColorThreshold,
  upscaler,
  setUpscaler,
  upscaleRatio,
  setUpscaleRatio,
  revertUpscaling,
  setRevertUpscaling,
}) => {
  const isColorizerActive = colorizer !== "none" || colorizeOnly;

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      {/* Colorization Card */}
      <div className="rounded-lg border border-zinc-200/60 bg-zinc-50/50 p-3 dark:border-zinc-800/60 dark:bg-zinc-800/25 space-y-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center space-x-1.5 text-xs font-bold uppercase tracking-wider text-purple-600 dark:text-purple-400">
            <Icon icon="carbon:color-palette" className="h-3.5 w-3.5" />
            <span>Colorization</span>
          </div>
          <label className="flex cursor-pointer items-center space-x-1.5 text-xs font-medium text-zinc-700 select-none dark:text-zinc-300">
            <input
              type="checkbox"
              checked={colorizeOnly}
              onChange={(e) => {
                const checked = e.target.checked;
                setColorizeOnly(checked);
                if (checked && colorizer === "none") setColorizer("mc2");
              }}
              className="h-3.5 w-3.5 rounded border-zinc-300 text-purple-600 focus:ring-purple-500 dark:border-zinc-600"
            />
            <span className={colorizeOnly ? "font-semibold text-purple-600 dark:text-purple-400" : ""}>
              Colorize Only (Skip text)
            </span>
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <select
            id="colorizerSelect"
            value={colorizer}
            onChange={(e) => {
              const val = e.target.value;
              setColorizer(val);
              if (val === "none") setColorizeOnly(false);
            }}
            className="flex-1 min-w-[140px] rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-800 shadow-xs focus:border-purple-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
          >
            {colorizerOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>

          {isColorizerActive && (
            <div className="flex items-center gap-2 text-xs">
              <div className="flex items-center space-x-1">
                <span className="text-zinc-500">Size:</span>
                <select
                  value={colorizationSize}
                  onChange={(e) => setColorizationSize(e.target.value)}
                  className="rounded-md border border-zinc-200 bg-white px-2 py-1 text-xs text-zinc-800 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
                >
                  {colorizationSizes.map((s) => (
                    <option key={s} value={String(s)}>
                      {s}px
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex items-center space-x-1">
                <span className="text-zinc-500" title="CIELAB color distance tolerance to skip already colored pages">Tol:</span>
                <input
                  type="number"
                  step={1}
                  value={colorThreshold}
                  onChange={(e) => setColorThreshold(Number(e.target.value))}
                  className="w-12 rounded-md border border-zinc-200 bg-white px-1.5 py-1 text-center text-xs text-zinc-800 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
                  title="Color tolerance to skip colored pages (default 31, -1 to disable)"
                />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Upscaling Card */}
      <div className="rounded-lg border border-zinc-200/60 bg-zinc-50/50 p-3 dark:border-zinc-800/60 dark:bg-zinc-800/25 space-y-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center space-x-1.5 text-xs font-bold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">
            <Icon icon="carbon:zoom-in" className="h-3.5 w-3.5" />
            <span>Upscaling & Resolution</span>
          </div>
          <label
            className={`flex items-center gap-1.5 text-xs font-medium ${
              upscaleRatio
                ? "cursor-pointer text-zinc-700 dark:text-zinc-300"
                : "cursor-not-allowed text-zinc-400 opacity-60 dark:text-zinc-500"
            }`}
          >
            <input
              type="checkbox"
              checked={Boolean(upscaleRatio && revertUpscaling)}
              disabled={!upscaleRatio}
              onChange={(e) => setRevertUpscaling(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-zinc-300 text-emerald-600 focus:ring-emerald-500 dark:border-zinc-600"
            />
            <span title="Downscale the final result back to the original image dimensions">
              Revert to original size
            </span>
          </label>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <select
            id="upscaler"
            value={upscaler}
            onChange={(e) => setUpscaler(e.target.value)}
            className="rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-800 shadow-xs focus:border-emerald-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
          >
            {upscalerOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>

          <select
            id="upscaleRatio"
            value={upscaleRatio}
            onChange={(e) => {
              const val = e.target.value;
              setUpscaleRatio(val);
              if (!val) setRevertUpscaling(false);
            }}
            className="rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-800 shadow-xs focus:border-emerald-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
          >
            {upscaleRatioOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.value ? `Upscale ${opt.label}` : "Disabled (1x)"}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
};
