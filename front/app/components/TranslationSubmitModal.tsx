import React, { useEffect, useMemo, useState } from "react";
import { Icon } from "@iconify/react";
import { ImageThumb, LargePagePreview, SplitHandle } from "./TranslationSubmitParts";
import { MANGA_TITLE_MAX_LENGTH } from "@/config";
import type { MangaGroupSelection, StoryPlan, StudioFile } from "@/types";
import {
  addStorySplit,
  addStorySplitAt,
  buildStoryPlan,
  mergeStoryPlan,
  removeStorySplit,
  removeStorySplitAt,
  renameStorySegment,
  updateStoryBoundary,
  validateStoryPlan,
} from "@/utils/storyPlan";
import { filterExistingGroups, normalizeExistingGroups, type ExistingGroupEntry, type ExistingGroupItem } from "@/components/GroupSelectionModal";

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (selection: MangaGroupSelection, storyPlan: StoryPlan) => void;
  files: StudioFile[];
  initialGroupName?: string;
  initialGroupId?: string | null;
  existingGroups: ExistingGroupEntry[];
  isLoadingGroups?: boolean;
  errorMessage?: string | null;
  isSubmitting?: boolean;
}

const STORY_COLORS = [
  "#6c5ce7",
  "#00b894",
  "#e17055",
  "#0984e3",
  "#e84393",
  "#fdcb6e",
  "#00cec9",
  "#a29bfe",
  "#fab1a0",
  "#74b9ff",
];

export const TranslationSubmitModal: React.FC<Props> = ({
  isOpen,
  onClose,
  onConfirm,
  files,
  initialGroupName = "",
  initialGroupId = null,
  existingGroups,
  isLoadingGroups = false,
  errorMessage = null,
  isSubmitting = false,
}) => {
  const [step, setStep] = useState<1 | 2>(1);
  const [groupName, setGroupName] = useState(initialGroupName);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(initialGroupId);
  const [searchQuery, setSearchQuery] = useState("");
  const [plan, setPlan] = useState<StoryPlan>(() => buildStoryPlan(files));
  const [selectedPreviewPage, setSelectedPreviewPage] = useState<number>(1);
  const [previewMode, setPreviewMode] = useState<"page" | "cutoff">("page");
  const [inspectBoundary, setInspectBoundary] = useState<number | null>(null);

  const groups = useMemo(() => normalizeExistingGroups(existingGroups), [existingGroups]);
  const filteredGroups = useMemo(() => filterExistingGroups(groups, searchQuery), [groups, searchQuery]);
  const orderedFiles = useMemo(() => [...files].sort((a, b) => a.dropOrder - b.dropOrder), [files]);
  const totalPages = orderedFiles.length;
  const validation = validateStoryPlan(plan, totalPages);

  useEffect(() => {
    if (!isOpen) return;
    setStep(1);
    const clean = initialGroupName.trim() === "Ungrouped" ? "" : initialGroupName.trim();
    setGroupName(clean);
    setSelectedGroupId(initialGroupId);
    setSearchQuery("");
    setPlan(buildStoryPlan(files));
    setSelectedPreviewPage(1);
    setPreviewMode("page");
    setInspectBoundary(null);
  }, [isOpen, initialGroupName, initialGroupId, files]);

  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isSubmitting) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isOpen, isSubmitting, onClose]);

  if (!isOpen) return null;

  const matchedExistingGroup = groups.find((group) => group.title.toLowerCase() === groupName.trim().toLowerCase());
  const selectedUngrouped = !groupName.trim() || groupName.trim().toLowerCase() === "ungrouped";
  const selection: MangaGroupSelection = {
    title: groupName.trim() || "Ungrouped",
    groupId: selectedUngrouped ? null : matchedExistingGroup?.id || selectedGroupId || null,
    isNewGroup: !selectedUngrouped && !matchedExistingGroup && !selectedGroupId,
  };

  const chooseGroup = (group: ExistingGroupItem) => {
    setGroupName(group.title);
    setSelectedGroupId(group.id || null);
  };

  const submit = (event?: React.FormEvent) => {
    event?.preventDefault();
    if (isSubmitting) return;
    if (step === 1) {
      setStep(2);
      return;
    }
    if (plan.enabled && !validation.valid) return;
    onConfirm(selection, plan);
  };

  const toggleEnabled = (enabled: boolean) => setPlan((current) => ({ ...current, enabled }));
  const toggleMerged = (merged: boolean) =>
    setPlan((current) =>
      merged ? mergeStoryPlan(current) : buildStoryPlan(orderedFiles, current.enabled, current.autoDetect)
    );

  // Map each page number (1-based) to its story segment and assigned color
  const pageColorMap = new Map<number, string>();
  if (!plan.enabled) {
    for (let p = 1; p <= totalPages; p++) {
      pageColorMap.set(p, STORY_COLORS[0]);
    }
  } else {
    plan.segments.forEach((segment, idx) => {
      const color = STORY_COLORS[idx % STORY_COLORS.length];
      for (let p = segment.startPage; p <= segment.endPage; p++) {
        pageColorMap.set(p, color);
      }
    });
  }

  const displayArchives = plan.mergeAllPages
    ? [{ id: "__merged__", name: "All pages", startPage: 1, endPage: totalPages, pageCount: totalPages }]
    : plan.archives;

  // Active split boundaries list across the plan
  const activeBoundaries = plan.segments
    .slice(0, -1)
    .map((s) => s.endPage)
    .filter((p) => p >= 1 && p < totalPages);

  // Determine active cut-off boundary to inspect
  const effectiveBoundary =
    inspectBoundary !== null && inspectBoundary >= 1 && inspectBoundary < totalPages
      ? inspectBoundary
      : activeBoundaries[0] ?? null;

  // Information for the currently previewed single page
  const activeFile = orderedFiles[selectedPreviewPage - 1] || orderedFiles[0];
  const activeSegmentIndex = plan.segments.findIndex(
    (s) => s.startPage <= selectedPreviewPage && selectedPreviewPage <= s.endPage
  );
  const activeSegment = plan.segments[activeSegmentIndex];
  const activeColor =
    !plan.enabled || activeSegmentIndex === -1
      ? STORY_COLORS[0]
      : STORY_COLORS[activeSegmentIndex % STORY_COLORS.length];

  // Information for the cut-off comparison mode (Page P vs Page P+1)
  const cutPageA = effectiveBoundary ?? 1;
  const cutPageB = (effectiveBoundary ?? 1) + 1;
  const fileA = orderedFiles[cutPageA - 1];
  const fileB = orderedFiles[cutPageB - 1];
  const segIndexA = plan.segments.findIndex((s) => s.startPage <= cutPageA && cutPageA <= s.endPage);
  const segIndexB = plan.segments.findIndex((s) => s.startPage <= cutPageB && cutPageB <= s.endPage);
  const segA = plan.segments[segIndexA];
  const segB = plan.segments[segIndexB];
  const colorA = segIndexA !== -1 ? STORY_COLORS[segIndexA % STORY_COLORS.length] : STORY_COLORS[0];
  const colorB = segIndexB !== -1 ? STORY_COLORS[segIndexB % STORY_COLORS.length] : STORY_COLORS[1];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-xs">
      <div
        className={`relative flex max-h-[min(92vh,900px)] w-full flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-2xl transition-all duration-200 dark:border-zinc-800 dark:bg-zinc-900 ${
          step === 1 ? "max-w-xl" : "max-w-6xl"
        }`}
      >
        {/* Header */}
        <div className="flex items-start justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-indigo-200 bg-indigo-50 text-indigo-600 dark:border-indigo-800 dark:bg-indigo-950/60 dark:text-indigo-400">
              <Icon icon="carbon:translate" className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                {step === 1 ? "Assign to Manga Group" : "Arrange stories"}
              </h2>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                Step {step} of 2 · {totalPages} {totalPages === 1 ? "page" : "pages"}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800"
            aria-label="Close"
          >
            <Icon icon="carbon:close" className="h-5 w-5" />
          </button>
        </div>

        {/* Modal Body */}
        <form id="translation-submit-form" onSubmit={submit} className="min-h-0 flex-1 overflow-y-auto p-5">
          {step === 1 ? (
            <div className="mx-auto max-w-xl space-y-4">
              <div className="space-y-1.5">
                <label
                  htmlFor="translation-group-input"
                  className="text-xs font-semibold uppercase tracking-wider text-zinc-600 dark:text-zinc-300"
                >
                  Group / Manga name
                </label>
                <input
                  id="translation-group-input"
                  autoFocus
                  value={groupName}
                  maxLength={MANGA_TITLE_MAX_LENGTH}
                  onChange={(event) => {
                    setGroupName(event.target.value);
                    setSelectedGroupId(null);
                  }}
                  placeholder="e.g. Chapter 01"
                  className="w-full rounded-xl border border-zinc-200 bg-zinc-50 px-3 py-2.5 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 dark:border-zinc-700 dark:bg-zinc-800/80"
                />
              </div>
              <div className="relative">
                <Icon
                  icon="carbon:search"
                  className="pointer-events-none absolute top-2.5 left-3 h-4 w-4 text-zinc-400"
                />
                <input
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder={isLoadingGroups ? "Loading existing groups…" : "Search existing groups"}
                  className="w-full rounded-xl border border-zinc-200 bg-white py-2.5 pr-3 pl-9 text-sm outline-none focus:border-indigo-500 dark:border-zinc-700 dark:bg-zinc-800/80"
                />
              </div>
              <div className="max-h-64 space-y-1 overflow-y-auto rounded-xl border border-zinc-200 p-1 dark:border-zinc-700">
                {!searchQuery.trim() ? (
                  <p className="px-3 py-5 text-center text-sm text-zinc-500">
                    Search to show matching manga groups.
                  </p>
                ) : filteredGroups.map((group) => (
                  <button
                    type="button"
                    key={`${group.id || group.title}`}
                    onClick={() => chooseGroup(group)}
                    className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm hover:bg-indigo-50 dark:hover:bg-indigo-950/40"
                  >
                    <span className="min-w-0 whitespace-normal break-all">{group.title}</span>
                    <span className="text-xs text-indigo-700 dark:text-indigo-300">{group.count ?? ""}</span>
                  </button>
                ))}
                {searchQuery.trim() && !filteredGroups.length && (
                  <p className="px-3 py-5 text-center text-sm text-zinc-500">
                    No matching groups. A new group will be created.
                  </p>
                )}
              </div>
              <div
                className={`rounded-xl border px-3 py-2 text-xs ${
                  selectedUngrouped
                    ? "border-zinc-200 text-indigo-800 dark:border-zinc-700 dark:text-indigo-200"
                    : "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300"
                }`}
              >
                {selectedUngrouped
                  ? "Pages will be kept ungrouped."
                  : matchedExistingGroup || selectedGroupId
                    ? `Adding to existing group “${selection.title}”.`
                    : `Creating new group “${selection.title}”.`}
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_360px] xl:grid-cols-[minmax(0,1fr)_420px]">
              {/* Left Column: Filmstrips, Boundary Controls, Story List */}
              <div className="space-y-4">
                {/* Boundary Banner */}
                <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-indigo-200/80 bg-indigo-50/60 p-4 dark:border-indigo-900/60 dark:bg-indigo-950/30">
                  <div>
                    <strong className="block text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                      Story boundaries
                    </strong>
                    <span className="text-xs text-zinc-600 dark:text-zinc-400">
                      Drag a handle to move a split, or click between pages to add one.
                    </span>
                  </div>
                  <label className="flex cursor-pointer items-center gap-2.5 text-xs font-medium text-zinc-800 select-none sm:text-sm dark:text-zinc-200">
                    Use story boundaries
                    <button
                      type="button"
                      role="switch"
                      aria-checked={plan.enabled}
                      onClick={() => toggleEnabled(!plan.enabled)}
                      className={`relative inline-flex h-5.5 w-10 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
                        plan.enabled ? "bg-indigo-600" : "bg-zinc-300 dark:bg-zinc-700"
                      }`}
                    >
                      <span
                        className={`pointer-events-none inline-block h-4.5 w-4.5 transform rounded-full bg-white shadow-md ring-0 transition duration-200 ease-in-out ${
                          plan.enabled ? "translate-x-4.5" : "translate-x-0"
                        }`}
                      />
                    </button>
                  </label>
                </div>

                {/* Options */}
                {plan.enabled && (
                  <div className="flex flex-wrap items-center gap-5 text-xs text-zinc-700 sm:text-sm dark:text-zinc-300">
                    <label className="flex cursor-pointer items-center gap-2 select-none">
                      <input
                        type="checkbox"
                        checked={plan.autoDetect}
                        onChange={(event) => setPlan((current) => ({ ...current, autoDetect: event.target.checked }))}
                        className="h-4 w-4 rounded-sm border-zinc-300 text-indigo-600 accent-indigo-600 focus:ring-indigo-500 dark:border-zinc-700"
                      />
                      <span>Auto-detect additional breaks after OCR</span>
                    </label>
                    <label className="flex cursor-pointer items-center gap-2 select-none">
                      <input
                        type="checkbox"
                        checked={plan.mergeAllPages}
                        onChange={(event) => toggleMerged(event.target.checked)}
                        className="h-4 w-4 rounded-sm border-zinc-300 text-indigo-600 accent-indigo-600 focus:ring-indigo-500 dark:border-zinc-700"
                      />
                      <span>Treat all pages as one story</span>
                    </label>
                  </div>
                )}

                {/* Cards per Archive or Merged */}
                <div className="space-y-4">
                  {displayArchives.map((archive) => {
                    const archiveFiles = orderedFiles.slice(archive.startPage - 1, archive.endPage);
                    const archiveSegments = plan.segments
                      .map((segment, index) => ({ segment, index }))
                      .filter(
                        ({ segment }) =>
                          segment.endPage >= archive.startPage &&
                          segment.startPage <= archive.endPage &&
                          (!plan.mergeAllPages ||
                            segment.startPage === archive.startPage ||
                            segment.endPage <= archive.endPage)
                      );

                    const canSplitArchive =
                      plan.enabled &&
                      archiveSegments.some(({ segment }) => segment.endPage > segment.startPage);

                    return (
                      <section
                        key={archive.id}
                        className="space-y-4 rounded-2xl border border-zinc-200 bg-white p-5 shadow-xs dark:border-zinc-800 dark:bg-zinc-900/90"
                      >
                        {/* Archive Header */}
                        <div className="flex items-center justify-between gap-3">
                          <div>
                            <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{archive.name}</h3>
                            <p className="text-xs text-zinc-500 dark:text-zinc-400">
                              Pages {archive.startPage}–{archive.endPage} · {archive.pageCount}{" "}
                              {archive.pageCount === 1 ? "page" : "pages"}
                            </p>
                          </div>
                          <span className="rounded-full border border-zinc-200 bg-zinc-100 px-3 py-1 font-mono text-xs font-semibold text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200">
                            {archiveSegments.length} {archiveSegments.length === 1 ? "story" : "stories"}
                          </span>
                        </div>

                        {/* Filmstrip with inline split controls */}
                        <div className="overflow-x-auto overflow-y-visible py-2">
                          <div
                            className={`flex w-max min-w-full items-end pb-3 transition-opacity duration-150 ${
                              !plan.enabled ? "pointer-events-none opacity-40" : ""
                            }`}
                          >
                            {archiveFiles.map((entry, fileIndex) => {
                              const p = archive.startPage + fileIndex;
                              const color = pageColorMap.get(p) || STORY_COLORS[0];
                              const isLastPageInArchive = p === archive.endPage;
                              const isSelected = selectedPreviewPage === p && previewMode === "page";

                              // Check if there is an active split boundary after page p
                              const splitIndex = plan.segments.findIndex(
                                (s, sIdx) => sIdx < plan.segments.length - 1 && s.endPage === p
                              );
                              const isSplit = plan.enabled && splitIndex !== -1;

                              return (
                                <React.Fragment key={entry.id}>
                                  {/* Page Thumbnail Cell */}
                                  <div className="flex shrink-0 flex-col items-center">
                                    <div
                                      className="overflow-hidden rounded-md border-2 transition-all duration-150"
                                      style={{ borderColor: isSelected ? "#6366f1" : `${color}88` }}
                                    >
                                      <ImageThumb
                                        file={entry.file}
                                        pageNum={p}
                                        isSelected={isSelected}
                                        onClick={() => {
                                          setSelectedPreviewPage(p);
                                          setPreviewMode("page");
                                          setInspectBoundary(null);
                                        }}
                                      />
                                    </div>
                                    <div className="mt-1.5 h-1 w-full rounded-xs" style={{ backgroundColor: color }} />
                                    <span className="mt-1 font-mono text-[10.5px] text-zinc-500 dark:text-zinc-400">
                                      {p}
                                    </span>
                                  </div>

                                  {/* Gap between pages */}
                                  {!isLastPageInArchive && (
                                    <div className="group/gap relative flex h-20 w-3.5 shrink-0 items-center justify-center">
                                      {isSplit ? (
                                        <SplitHandle
                                          afterPage={p}
                                          minPage={plan.segments[splitIndex].startPage}
                                          maxPage={plan.segments[splitIndex + 1].endPage - 1}
                                          onUpdateBoundary={(_curr, newP) => {
                                            setPlan((current) => updateStoryBoundary(current, splitIndex, newP));
                                            setInspectBoundary(newP);
                                          }}
                                          onRemove={(afterP) => {
                                            setPlan((current) => removeStorySplitAt(current, afterP));
                                            if (inspectBoundary === afterP) setInspectBoundary(null);
                                          }}
                                          onHoverStart={(afterP) => {
                                            setInspectBoundary(afterP);
                                            setPreviewMode("cutoff");
                                          }}
                                          onHoverEnd={() => {
                                            // keep inspection active or user can click
                                          }}
                                          onDragMove={(pendingP) => {
                                            setInspectBoundary(pendingP);
                                            setPreviewMode("cutoff");
                                          }}
                                          disabled={!plan.enabled}
                                        />
                                      ) : (
                                        <>
                                          <div className="h-full w-px bg-zinc-200 dark:bg-zinc-800" />
                                          {plan.enabled && (
                                            <button
                                              type="button"
                                              onClick={() => {
                                                setPlan((current) => addStorySplitAt(current, p));
                                                setInspectBoundary(p);
                                                setPreviewMode("cutoff");
                                              }}
                                              title={`Split after page ${p}`}
                                              className="absolute z-10 flex h-5 w-5 cursor-pointer items-center justify-center rounded-full border border-zinc-300 bg-white text-xs font-bold text-zinc-600 opacity-0 shadow-xs transition-all group-hover/gap:opacity-100 hover:scale-110 hover:border-indigo-600 hover:bg-indigo-600 hover:text-white dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:border-indigo-600 dark:hover:bg-indigo-600 dark:hover:text-white"
                                            >
                                              +
                                            </button>
                                          )}
                                        </>
                                      )}
                                    </div>
                                  )}
                                </React.Fragment>
                              );
                            })}
                          </div>
                        </div>

                        {/* Story Rows List */}
                        <div className="space-y-2">
                          {archiveSegments.map(({ segment, index }) => {
                            const segColor = STORY_COLORS[index % STORY_COLORS.length];
                            return (
                              <div
                                key={segment.id}
                                className="flex items-center gap-3 rounded-xl border border-zinc-200/90 bg-zinc-50/80 p-2.5 dark:border-zinc-800 dark:bg-zinc-800/50"
                              >
                                <div
                                  className="h-8.5 w-2.5 shrink-0 rounded-sm"
                                  style={{ backgroundColor: segColor }}
                                />
                                <input
                                  value={segment.label || ""}
                                  onChange={(event) =>
                                    setPlan((current) => renameStorySegment(current, segment.id, event.target.value))
                                  }
                                  disabled={!plan.enabled}
                                  aria-label={`Story ${index + 1} label`}
                                  placeholder={`Story ${index + 1}`}
                                  className="w-36 shrink-0 rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs font-medium outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 sm:w-44 sm:text-sm dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100"
                                />
                                <span className="rounded-md bg-zinc-200/70 px-2 py-1 font-mono text-xs text-zinc-600 dark:bg-zinc-700/60 dark:text-zinc-300">
                                  pages {segment.startPage}–{segment.endPage}
                                </span>
                                <span className="ml-auto text-xs font-medium text-zinc-500 dark:text-zinc-400">
                                  {segment.endPage - segment.startPage + 1}{" "}
                                  {segment.endPage - segment.startPage + 1 === 1 ? "page" : "pages"}
                                </span>
                                <button
                                  type="button"
                                  disabled={!plan.enabled || archiveSegments.length <= 1}
                                  onClick={() => setPlan((current) => removeStorySplit(current, index))}
                                  title={
                                    archiveSegments.length <= 1
                                      ? "Cannot remove only story"
                                      : `Remove split for story ${index + 1}`
                                  }
                                  className="flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-lg border border-transparent text-zinc-400 transition-colors hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-20 dark:hover:border-rose-900/60 dark:hover:bg-rose-950/40 dark:hover:text-rose-400"
                                >
                                  <Icon icon="carbon:trash-can" className="h-4 w-4" />
                                </button>
                              </div>
                            );
                          })}
                        </div>

                        {/* Add Split in this Archive Button */}
                        <button
                          type="button"
                          onClick={() => {
                            setPlan((current) =>
                              addStorySplit(current, plan.mergeAllPages ? undefined : archive.id)
                            );
                          }}
                          disabled={!plan.enabled || !canSplitArchive}
                          className="flex w-full cursor-pointer items-center justify-center gap-1.5 rounded-xl border border-dashed border-zinc-300 px-3 py-2.5 text-xs font-medium text-zinc-600 transition-all hover:border-indigo-500 hover:bg-indigo-50/50 hover:text-indigo-600 disabled:cursor-not-allowed disabled:opacity-30 sm:text-sm dark:border-zinc-700 dark:text-zinc-400 dark:hover:border-indigo-500 dark:hover:bg-indigo-950/20 dark:hover:text-indigo-400"
                        >
                          <Icon icon="carbon:add" className="h-4 w-4" /> Add story split
                        </button>

                        {/* Help Tip */}
                        <p className="text-[11.5px] leading-relaxed text-zinc-500 dark:text-zinc-400">
                          Tip: Click any thumbnail to preview it on the right. Drag a purple handle to adjust a split,
                          hover a gap to click “+” and add a cut, or click “×” to remove it.
                        </p>
                      </section>
                    );
                  })}
                </div>

                {/* Validation Alert */}
                {plan.enabled && !validation.valid && (
                  <div
                    role="alert"
                    className="rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300"
                  >
                    {validation.errors.join(" ")}
                  </div>
                )}

                {/* Summary Bar */}
                <div className="rounded-xl border border-zinc-200 bg-zinc-100 px-4 py-2.5 text-xs font-medium text-zinc-600 dark:border-zinc-700/60 dark:bg-zinc-800/70 dark:text-zinc-300">
                  {plan.archives.length} {plan.archives.length === 1 ? "archive" : "archives"} · {totalPages} pages ·{" "}
                  {plan.enabled ? plan.segments.length : 1}{" "}
                  {!plan.enabled || plan.segments.length === 1 ? "story" : "stories"}
                </div>
              </div>

              {/* Right Column: Page Preview & Cut-off Inspector Panel */}
              <div className="sticky top-0 flex flex-col space-y-3 rounded-2xl border border-zinc-200 bg-zinc-50/80 p-4 shadow-xs dark:border-zinc-800 dark:bg-zinc-900/80">
                {/* Preview Tabs / Mode Switch */}
                <div className="flex items-center justify-between gap-2 border-b border-zinc-200 pb-3 dark:border-zinc-800">
                  <div className="flex items-center gap-1.5 rounded-lg bg-zinc-200/70 p-1 dark:bg-zinc-800">
                    <button
                      type="button"
                      onClick={() => setPreviewMode("page")}
                      className={`cursor-pointer rounded-md px-2.5 py-1 text-xs font-medium transition-all ${
                        previewMode === "page"
                          ? "bg-white text-zinc-900 shadow-xs dark:bg-zinc-700 dark:text-zinc-100"
                          : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-200"
                      }`}
                    >
                      Page View
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setPreviewMode("cutoff");
                        if (effectiveBoundary === null && activeBoundaries.length > 0) {
                          setInspectBoundary(activeBoundaries[0]);
                        }
                      }}
                      disabled={activeBoundaries.length === 0}
                      className={`cursor-pointer rounded-md px-2.5 py-1 text-xs font-medium transition-all disabled:cursor-not-allowed disabled:opacity-40 ${
                        previewMode === "cutoff"
                          ? "bg-white text-zinc-900 shadow-xs dark:bg-zinc-700 dark:text-zinc-100"
                          : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-200"
                      }`}
                    >
                      Cut-off View ({activeBoundaries.length})
                    </button>
                  </div>

                  {/* Navigation Buttons */}
                  {previewMode === "page" ? (
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => setSelectedPreviewPage((p) => Math.max(1, p - 1))}
                        disabled={selectedPreviewPage <= 1}
                        title="Previous page"
                        className="cursor-pointer rounded-lg p-1 text-zinc-500 hover:bg-zinc-200 hover:text-zinc-800 disabled:cursor-not-allowed disabled:opacity-30 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                      >
                        <Icon icon="carbon:chevron-left" className="h-4 w-4" />
                      </button>
                      <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
                        {selectedPreviewPage}/{totalPages}
                      </span>
                      <button
                        type="button"
                        onClick={() => setSelectedPreviewPage((p) => Math.min(totalPages, p + 1))}
                        disabled={selectedPreviewPage >= totalPages}
                        title="Next page"
                        className="cursor-pointer rounded-lg p-1 text-zinc-500 hover:bg-zinc-200 hover:text-zinc-800 disabled:cursor-not-allowed disabled:opacity-30 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                      >
                        <Icon icon="carbon:chevron-right" className="h-4 w-4" />
                      </button>
                    </div>
                  ) : (
                    activeBoundaries.length > 1 && (
                      <div className="flex items-center gap-1">
                        <span className="text-[11px] text-zinc-500">Cut:</span>
                        <select
                          value={effectiveBoundary ?? activeBoundaries[0]}
                          onChange={(e) => setInspectBoundary(Number(e.target.value))}
                          className="cursor-pointer rounded-md border border-zinc-200 bg-white px-1.5 py-0.5 font-mono text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
                        >
                          {activeBoundaries.map((b) => (
                            <option key={b} value={b}>
                              p{b} / p{b + 1}
                            </option>
                          ))}
                        </select>
                      </div>
                    )
                  )}
                </div>

                {/* Preview Content */}
                {previewMode === "page" ? (
                  /* Single Page View */
                  <div className="flex flex-col space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-2 truncate">
                        <span
                          className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                          style={{ backgroundColor: activeColor }}
                        />
                        <span className="truncate font-semibold text-zinc-800 dark:text-zinc-200">
                          {activeSegment?.label || `Story ${activeSegmentIndex + 1}`}
                        </span>
                        {activeSegment && (
                          <span className="font-mono text-[11px] text-zinc-500">
                            (p{activeSegment.startPage}–p{activeSegment.endPage})
                          </span>
                        )}
                      </div>
                      <span className="font-mono text-[11px] text-zinc-400">
                        {activeFile?.archiveName || "Page"}
                      </span>
                    </div>

                    {/* Image Box */}
                    <div className="relative flex h-[380px] w-full items-center justify-center overflow-hidden rounded-xl border border-zinc-200/80 bg-zinc-950 p-1 shadow-inner dark:border-zinc-800">
                      {activeFile && (
                        <LargePagePreview file={activeFile.file} pageNum={selectedPreviewPage} />
                      )}
                    </div>

                    {/* Quick cut-off jump if adjacent to a split */}
                    {activeBoundaries.includes(selectedPreviewPage) && (
                      <button
                        type="button"
                        onClick={() => {
                          setInspectBoundary(selectedPreviewPage);
                          setPreviewMode("cutoff");
                        }}
                        className="flex cursor-pointer items-center justify-center gap-1 rounded-lg bg-indigo-50 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100 dark:bg-indigo-950/40 dark:text-indigo-300 dark:hover:bg-indigo-950/70"
                      >
                        <Icon icon="carbon:compare" className="h-3.5 w-3.5" />
                        Inspect cut-off after page {selectedPreviewPage}
                      </button>
                    )}
                  </div>
                ) : (
                  /* Cut-off Boundary Comparison View */
                  <div className="flex flex-col space-y-2.5">
                    <div className="flex items-center justify-between">
                      <div className="text-xs">
                        <span className="font-semibold text-zinc-800 dark:text-zinc-200">
                          Cut-off Boundary
                        </span>
                        <span className="ml-1.5 font-mono text-[11px] text-indigo-600 dark:text-indigo-400">
                          after page {effectiveBoundary}
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={() => setPreviewMode("page")}
                        className="cursor-pointer text-[11px] text-zinc-500 underline hover:text-zinc-800 dark:hover:text-zinc-300"
                      >
                        Back to single
                      </button>
                    </div>

                    {/* 2-Up Side-by-side Page Comparison */}
                    <div className="grid grid-cols-2 gap-2">
                      {/* Left: Last Page of Story A */}
                      <div className="flex flex-col space-y-1">
                        <div
                          className="flex items-center gap-1.5 truncate rounded-md px-2 py-1 text-[11px] font-medium"
                          style={{ backgroundColor: `${colorA}20`, color: colorA }}
                        >
                          <span className="truncate">{segA?.label || "Story A"}</span>
                          <span className="font-mono text-[10px] opacity-80">(p{cutPageA})</span>
                        </div>
                        <div className="relative flex h-[280px] w-full items-center justify-center overflow-hidden rounded-lg border border-zinc-200 bg-zinc-950 p-1 shadow-inner dark:border-zinc-800">
                          {fileA && <LargePagePreview file={fileA.file} pageNum={cutPageA} />}
                        </div>
                        <span className="text-center font-mono text-[10.5px] text-zinc-500">
                          End of story
                        </span>
                      </div>

                      {/* Right: First Page of Story B */}
                      <div className="flex flex-col space-y-1">
                        <div
                          className="flex items-center gap-1.5 truncate rounded-md px-2 py-1 text-[11px] font-medium"
                          style={{ backgroundColor: `${colorB}20`, color: colorB }}
                        >
                          <span className="truncate">{segB?.label || "Story B"}</span>
                          <span className="font-mono text-[10px] opacity-80">(p{cutPageB})</span>
                        </div>
                        <div className="relative flex h-[280px] w-full items-center justify-center overflow-hidden rounded-lg border border-zinc-200 bg-zinc-950 p-1 shadow-inner dark:border-zinc-800">
                          {fileB && <LargePagePreview file={fileB.file} pageNum={cutPageB} />}
                        </div>
                        <span className="text-center font-mono text-[10.5px] text-zinc-500">
                          Start of story
                        </span>
                      </div>
                    </div>

                    <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
                      Compare the last page of {segA?.label || "previous story"} with the first page of{" "}
                      {segB?.label || "next story"} to confirm the break.
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}

          {errorMessage && (
            <div
              role="alert"
              className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300"
            >
              {errorMessage}
            </div>
          )}
        </form>

        {/* Modal Footer */}
        <div className="flex items-center justify-between border-t border-zinc-200 px-5 py-4 dark:border-zinc-800">
          <button
            type="button"
            onClick={() => (step === 1 ? onClose() : setStep(1))}
            disabled={isSubmitting}
            className="cursor-pointer rounded-lg px-3 py-2 text-sm text-zinc-800 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            {step === 1 ? "Cancel" : "Back"}
          </button>
          <button
            type="submit"
            form="translation-submit-form"
            disabled={isSubmitting || (step === 2 && plan.enabled && !validation.valid)}
            className="cursor-pointer rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isSubmitting ? "Submitting…" : step === 1 ? "Arrange stories" : "Start translation"}
          </button>
        </div>
      </div>
    </div>
  );
};
