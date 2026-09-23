import React, { useEffect, useState } from "react";
import { Icon } from "@iconify/react";
import { Link } from "react-router";

interface HeaderProps {
  theme: "light" | "dark";
  onToggleTheme: () => void;
  activeView?: "studio" | "gallery" | "search";
  gallerySection?: "manga" | "series";
  onSelectView?: (view: "studio" | "gallery") => void;
  galleryCount?: number;
  jobCount?: number;
  jobAttentionCount?: number;
  onOpenJobs?: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  theme,
  onToggleTheme,
  activeView = "studio",
  gallerySection = "manga",
  onSelectView,
  galleryCount = 0,
  jobCount = 0,
  jobAttentionCount = 0,
  onOpenJobs,
}) => {
  const [showShortcuts, setShowShortcuts] = useState(false);

  useEffect(() => {
    if (!showShortcuts) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setShowShortcuts(false);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [showShortcuts]);

  return (
    <>
      <header className="sticky top-0 z-40 w-full border-b border-zinc-200 dark:border-zinc-800 bg-white/80 dark:bg-zinc-900/80 backdrop-blur-md transition-colors">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="flex flex-wrap items-center justify-between gap-2 py-2 sm:h-16 sm:flex-nowrap sm:gap-4 sm:py-0">
            {/* Logo & Title */}
            <Link
              to="/studio"
              aria-label="MangaStudio home"
              className="flex min-w-0 items-center space-x-2 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 sm:space-x-3"
            >
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-white shadow-sm shadow-indigo-500/20">
                <Icon icon="carbon:translate" className="h-5 w-5" />
              </div>
              <div className="flex items-baseline space-x-2">
                <span className="truncate text-base font-bold tracking-tight text-zinc-900 dark:text-zinc-50 sm:text-lg">
                  Manga<span className="text-indigo-600 dark:text-indigo-400">Studio</span>
                </span>
                <span className="hidden text-xs font-medium uppercase tracking-wider text-zinc-400 dark:text-zinc-500 sm:inline-block">
                  Translator
                </span>
              </div>
            </Link>

            {/* View Navigation Tabs */}
            <nav className="order-3 flex w-full min-w-0 items-center space-x-0.5 overflow-x-auto rounded-lg bg-zinc-100 p-1 text-xs font-medium dark:bg-zinc-800/70 sm:order-none sm:w-auto sm:space-x-1 sm:overflow-visible sm:text-sm">
              <Link
                to="/studio"
                onClick={() => onSelectView?.("studio")}
                className={`flex min-w-0 flex-none items-center justify-center space-x-1 rounded-md px-2 py-1.5 transition-colors sm:space-x-1.5 sm:px-3 ${
                  activeView === "studio"
                    ? "bg-white dark:bg-zinc-700 text-zinc-900 dark:text-zinc-50 shadow-xs"
                    : "text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200"
                }`}
              >
                <Icon icon="carbon:edit" className="h-4 w-4" />
                <span className="truncate">Studio</span>
              </Link>
              <Link
                to="/gallery"
                onClick={() => onSelectView?.("gallery")}
                className={`flex min-w-0 flex-none items-center justify-center space-x-1 rounded-md px-2 py-1.5 transition-colors sm:space-x-1.5 sm:px-3 ${
                  activeView === "gallery" && gallerySection !== "series"
                    ? "bg-white dark:bg-zinc-700 text-zinc-900 dark:text-zinc-50 shadow-xs"
                    : "text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200"
                }`}
              >
                <Icon icon="carbon:image" className="h-4 w-4" />
                <span className="truncate">Gallery</span>
                {galleryCount > 0 && (
                  <span className="ml-1 hidden rounded-full bg-indigo-100 px-1.5 py-0.2 text-xs font-semibold text-indigo-700 dark:bg-indigo-900/60 dark:text-indigo-300 sm:inline">
                    {galleryCount}
                  </span>
                )}
              </Link>
              <Link
                to="/gallery?view=series"
                onClick={() => onSelectView?.("gallery")}
                className={`flex min-w-0 flex-none items-center justify-center space-x-1 rounded-md px-2 py-1.5 transition-colors sm:space-x-1.5 sm:px-3 ${
                  activeView === "gallery" && gallerySection === "series"
                    ? "bg-white text-zinc-900 shadow-xs dark:bg-zinc-700 dark:text-zinc-50"
                    : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-200"
                }`}
              >
                <Icon icon="carbon:catalog" className="h-4 w-4" />
                <span className="truncate">Series</span>
              </Link>
              <Link to="/search-lab" aria-current={activeView === 'search' ? 'page' : undefined}
                className={`flex flex-none items-center rounded-md px-2 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 sm:px-3 ${activeView === 'search' ? 'bg-white text-zinc-900 shadow-xs dark:bg-zinc-700 dark:text-zinc-50' : 'text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-200'}`}>
                <Icon icon="carbon:search" className="mr-1.5 h-4 w-4" />Search Lab
              </Link>
            </nav>

            {/* Right Tools & Status */}
            <div className="ml-auto flex items-center space-x-1.5 sm:space-x-3">
              <button
                type="button"
                onClick={onOpenJobs}
                className="flex items-center gap-1.5 rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-1.5 text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:border-indigo-800/80 dark:bg-indigo-950/60 dark:text-indigo-300 dark:hover:bg-indigo-950 sm:px-3"
                aria-label={`Open jobs${jobCount ? `, ${jobCount} jobs` : ""}`}
              >
                <span className={`relative flex h-2 w-2 ${jobCount ? "" : "opacity-60"}`}>
                  {jobCount > 0 && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75" />}
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-indigo-500" />
                </span>
                <span>Jobs</span>
                {jobCount > 0 && <span className="tabular-nums">({jobCount})</span>}
                {jobAttentionCount > 0 && (
                  <span className="rounded-full bg-rose-500 px-1.5 py-0.5 text-xs leading-none text-white" aria-label={`${jobAttentionCount} jobs need attention`}>
                    {jobAttentionCount}
                  </span>
                )}
              </button>

              {/* Keyboard Shortcuts Trigger */}
              <button
                type="button"
                onClick={() => setShowShortcuts(true)}
                className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800 hover:text-zinc-800 dark:hover:text-zinc-100 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
                title="Keyboard shortcuts"
                aria-label="Keyboard shortcuts"
              >
                <Icon icon="carbon:keyboard" className="h-5 w-5" />
              </button>

              {/* Theme Toggle */}
              <button
                type="button"
                onClick={onToggleTheme}
                className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800 hover:text-zinc-800 dark:hover:text-zinc-100 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
                title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
                aria-label="Toggle theme"
              >
                {theme === "dark" ? (
                  <Icon icon="carbon:sun" className="h-5 w-5 text-amber-400" />
                ) : (
                  <Icon icon="carbon:moon" className="h-5 w-5 text-zinc-600" />
                )}
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Keyboard Shortcuts Modal */}
      {showShortcuts && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4"
        >
          <div
            className="w-full max-w-md rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 p-6 shadow-xl space-y-4"
          >
            <div className="flex items-center justify-between border-b border-zinc-100 dark:border-zinc-800 pb-3">
              <div className="flex items-center space-x-2">
                <Icon icon="carbon:keyboard" className="h-5 w-5 text-indigo-600 dark:text-indigo-400" />
                <h3 className="font-semibold text-zinc-900 dark:text-zinc-100">
                  Keyboard Shortcuts
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setShowShortcuts(false)}
                className="rounded-md p-1 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800"
              >
                <Icon icon="carbon:close" className="h-5 w-5" />
              </button>
            </div>

            <div className="space-y-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-zinc-600 dark:text-zinc-300">Paste screenshot</span>
                <kbd className="rounded border border-zinc-300 dark:border-zinc-700 bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 text-xs font-mono text-zinc-700 dark:text-zinc-300">
                  ⌘V / Ctrl+V
                </kbd>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-600 dark:text-zinc-300">Hold to compare original</span>
                <kbd className="rounded border border-zinc-300 dark:border-zinc-700 bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 text-xs font-mono text-zinc-700 dark:text-zinc-300">
                  Space / O
                </kbd>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-600 dark:text-zinc-300">Navigate in Lightbox</span>
                <kbd className="rounded border border-zinc-300 dark:border-zinc-700 bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 text-xs font-mono text-zinc-700 dark:text-zinc-300">
                  ← / →
                </kbd>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-600 dark:text-zinc-300">Close viewer / dialog</span>
                <kbd className="rounded border border-zinc-300 dark:border-zinc-700 bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 text-xs font-mono text-zinc-700 dark:text-zinc-300">
                  Esc
                </kbd>
              </div>
            </div>

            <div className="pt-2 text-right">
              <button
                type="button"
                onClick={() => setShowShortcuts(false)}
                className="rounded-lg bg-zinc-100 dark:bg-zinc-800 px-4 py-2 text-xs font-medium text-zinc-800 dark:text-zinc-200 hover:bg-zinc-200 dark:hover:bg-zinc-700 transition-colors"
              >
                Got it
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
