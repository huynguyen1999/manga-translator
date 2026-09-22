import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Icon } from '@iconify/react';
import { MANGA_TITLE_MAX_LENGTH } from '@/config';
import type { MangaGroupSelection } from '@/types';

export interface ExistingGroupItem {
  id?: string;
  title: string;
  count?: number;
}

export type ExistingGroupEntry = string | ExistingGroupItem;

interface GroupSelectionModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (selection: MangaGroupSelection | string) => void;
  initialGroupName?: string;
  initialGroupId?: string | null;
  existingGroups: ExistingGroupEntry[];
  pageCount: number;
  title?: string;
  subtitle?: string;
  actionLabel?: string;
  icon?: string;
  allowUngrouped?: boolean;
  isLoadingGroups?: boolean;
  errorMessage?: string | null;
  isSubmitting?: boolean;
}

export function normalizeExistingGroups(existingGroups: ExistingGroupEntry[]): ExistingGroupItem[] {
  const seen = new Set<string>();
  const result: ExistingGroupItem[] = [];
  for (const item of existingGroups) {
    if (typeof item === 'string') {
      const clean = item.trim();
      if (clean && clean.toLowerCase() !== 'ungrouped' && !seen.has(clean.toLowerCase())) {
        seen.add(clean.toLowerCase());
        result.push({ title: clean });
      }
    } else if (item && typeof item.title === 'string') {
      const clean = item.title.trim();
      if (clean && clean.toLowerCase() !== 'ungrouped' && !seen.has(clean.toLowerCase())) {
        seen.add(clean.toLowerCase());
        result.push({ id: item.id, title: clean, count: item.count });
      }
    }
  }
  return result;
}

export function filterExistingGroups(groups: ExistingGroupItem[], query: string): ExistingGroupItem[] {
  const clean = query.trim().toLowerCase();
  if (!clean) return groups;
  return groups.filter((grp) => grp.title.toLowerCase().includes(clean));
}

export const GroupSelectionModal: React.FC<GroupSelectionModalProps> = ({
  isOpen,
  onClose,
  onConfirm,
  initialGroupName = '',
  initialGroupId = null,
  existingGroups,
  pageCount,
  title,
  subtitle,
  actionLabel,
  icon = 'carbon:folder-add',
  allowUngrouped = true,
  isLoadingGroups = false,
  errorMessage = null,
  isSubmitting = false,
}) => {
  const [groupName, setGroupName] = useState(initialGroupName);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(initialGroupId);
  const [searchQuery, setSearchQuery] = useState('');
  const initializedOpenRef = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Normalize existing groups into objects
  const normalizedGroups = useMemo<ExistingGroupItem[]>(() => {
    return normalizeExistingGroups(existingGroups);
  }, [existingGroups]);

  // Sync initial value and auto-focus when modal opens
  useEffect(() => {
    if (!isOpen) {
      initializedOpenRef.current = false;
      return;
    }

    if (initializedOpenRef.current) return;
    initializedOpenRef.current = true;

    const cleanInitial = initialGroupName.trim() === 'Ungrouped' ? '' : initialGroupName.trim();
    setGroupName(cleanInitial);
    const matched = normalizedGroups.find(
      (g) => g.title.toLowerCase() === cleanInitial.toLowerCase()
    );
    setSelectedGroupId(matched?.id || initialGroupId || null);
    setSearchQuery('');
    inputRef.current?.focus();
  }, [isOpen, initialGroupName, initialGroupId, normalizedGroups]);

  // Handle Escape key
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isSubmitting) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, isSubmitting, onClose]);

  // Check if current input matches an existing group
  const matchedExistingGroup = useMemo(() => {
    const clean = groupName.trim().toLowerCase();
    if (!clean || clean === 'ungrouped') return null;
    return normalizedGroups.find((grp) => grp.title.toLowerCase() === clean) || null;
  }, [groupName, normalizedGroups]);

  const isCurrentUngrouped = !groupName.trim() || groupName.trim().toLowerCase() === 'ungrouped';
  const isExistingSelected = Boolean(matchedExistingGroup || selectedGroupId);

  // Filter existing groups based on search query
  const filteredGroups = useMemo(() => {
    return filterExistingGroups(normalizedGroups, searchQuery);
  }, [normalizedGroups, searchQuery]);

  const handleSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (filteredGroups.length > 0) {
        handleSelectGroup(filteredGroups[0]);
      }
    }
  };

  const handleNameChange = (newName: string) => {
    setGroupName(newName);
    const matched = normalizedGroups.find(
      (g) => g.title.toLowerCase() === newName.trim().toLowerCase()
    );
    setSelectedGroupId(matched?.id || null);
  };

  const handleSelectGroup = (group: ExistingGroupItem) => {
    setGroupName(group.title);
    setSelectedGroupId(group.id || null);
    inputRef.current?.focus();
  };

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (isSubmitting) return;
    const cleanName = groupName.trim() || 'Ungrouped';
    const isUngrouped = cleanName === 'Ungrouped';
    const effectiveGroupId = isUngrouped
      ? null
      : matchedExistingGroup?.id || selectedGroupId || null;
    const isNew = !isUngrouped && !matchedExistingGroup && !selectedGroupId;

    onConfirm({
      title: cleanName,
      groupId: effectiveGroupId,
      isNewGroup: isNew,
    });
  };

  if (!isOpen) return null;

  const shortName =
    groupName.trim().length > 18
      ? `${groupName.trim().slice(0, 18)}…`
      : groupName.trim();

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs"
    >
      <div
        className="relative w-full max-w-md rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 p-6 shadow-2xl space-y-5 select-none"
      >
        {/* Header */}
        <div className="flex items-start justify-between">
          <div className="flex items-center space-x-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-50 dark:bg-indigo-950/60 border border-indigo-200 dark:border-indigo-800 text-indigo-600 dark:text-indigo-400 shadow-xs">
              <Icon icon={icon} className="h-5 w-5" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                {title || 'Assign to Manga Group'}
              </h3>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                {subtitle || `${pageCount} ${pageCount === 1 ? 'page' : 'pages'}`}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            className="rounded-lg p-1.5 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
            title="Cancel (Esc)"
          >
            <Icon icon="carbon:close" className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Target Status Banner */}
          {!isCurrentUngrouped && (
            <div
              className={`flex items-center justify-between px-3 py-2 rounded-xl text-xs font-medium border transition-all ${
                isExistingSelected
                  ? 'bg-emerald-50 dark:bg-emerald-950/40 border-emerald-200 dark:border-emerald-800 text-emerald-800 dark:text-emerald-300'
                  : 'bg-indigo-50 dark:bg-indigo-950/40 border-indigo-200 dark:border-indigo-800 text-indigo-800 dark:text-indigo-300'
              }`}
            >
              <div className="flex items-center space-x-2 truncate">
                <Icon
                  icon={isExistingSelected ? 'carbon:checkmark-filled' : 'carbon:add-alt'}
                  className="h-4 w-4 shrink-0"
                />
                <span className="truncate">
                  {isExistingSelected
                    ? `Adding to existing group "${matchedExistingGroup?.title || groupName.trim()}"`
                    : `Creating new group "${groupName.trim()}"`}
                </span>
              </div>
              <span
                className={`text-[10px] px-2 py-0.5 rounded-full font-semibold shrink-0 ml-2 ${
                  isExistingSelected
                    ? 'bg-emerald-200/60 dark:bg-emerald-800/60 text-emerald-900 dark:text-emerald-200'
                    : 'bg-indigo-200/60 dark:bg-indigo-800/60 text-indigo-900 dark:text-indigo-200'
                }`}
              >
                {isExistingSelected
                  ? matchedExistingGroup?.count !== undefined
                    ? `${matchedExistingGroup.count} pages`
                    : 'Existing'
                  : 'New Group'}
              </span>
            </div>
          )}

          {/* Input field */}
          <div className="space-y-1.5">
            <label
              htmlFor="manga-group-input"
              className="block text-xs font-semibold text-zinc-700 dark:text-zinc-300"
            >
              Group / Manga Name
            </label>
            <div className="relative flex items-center">
              <div className="absolute left-3 pointer-events-none text-zinc-400">
                <Icon icon="carbon:book" className="h-4 w-4" />
              </div>
              <input
                ref={inputRef}
                id="manga-group-input"
                type="text"
                value={groupName}
                onChange={(e) => handleNameChange(e.target.value)}
                maxLength={MANGA_TITLE_MAX_LENGTH}
                placeholder="e.g., Chapter 01, Solo Leveling, etc."
                className="w-full rounded-xl border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800/80 pl-9.5 pr-9 py-2.5 text-sm text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:border-indigo-500 focus:bg-white dark:focus:bg-zinc-900 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 transition-all"
              />
              {groupName && (
                <button
                  type="button"
                  onClick={() => {
                    setGroupName('');
                    setSelectedGroupId(null);
                    inputRef.current?.focus();
                  }}
                  className="absolute right-3 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 p-0.5"
                  title="Clear"
                >
                  <Icon icon="carbon:close-outline" className="h-4 w-4" />
                </button>
              )}
            </div>
            <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
              Select an existing manga to add pages to it, or enter a new title to create a group.
            </p>
            {errorMessage && (
              <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/70 dark:bg-red-950/30 dark:text-red-300" role="alert">
                {errorMessage}
              </p>
            )}
          </div>

          {/* Existing Groups / Suggestions */}
          {(normalizedGroups.length > 0 || isLoadingGroups) && (
            <div className="space-y-2 pt-1">
              <div className="flex items-center justify-between text-xs text-zinc-500 dark:text-zinc-400">
                <span className="font-medium">Existing & Recent Groups:</span>
                <span className="text-[11px] text-zinc-400 dark:text-zinc-500 flex items-center space-x-1">
                  {isLoadingGroups && (
                    <Icon icon="carbon:renew" className="h-3 w-3 animate-spin inline-block mr-1" />
                  )}
                  <span>
                    {isLoadingGroups && normalizedGroups.length === 0
                      ? 'Loading…'
                      : searchQuery.trim()
                      ? `${filteredGroups.length} of ${normalizedGroups.length}`
                      : `${normalizedGroups.length} available`}
                  </span>
                </span>
              </div>

              {/* Search bar for filtering existing manga groups */}
              <div className="relative flex items-center">
                <div className="absolute left-2.5 pointer-events-none text-zinc-400">
                  <Icon icon="carbon:search" className="h-3.5 w-3.5" />
                </div>
                <input
                  ref={searchInputRef}
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={handleSearchKeyDown}
                  placeholder="Search existing manga..."
                  className="w-full rounded-lg border border-zinc-200 dark:border-zinc-700/80 bg-zinc-50 dark:bg-zinc-800/60 pl-8 pr-7 py-1.5 text-xs text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:border-indigo-500 focus:bg-white dark:focus:bg-zinc-900 focus:outline-none focus:ring-1 focus:ring-indigo-500/20 transition-all"
                />
                {searchQuery && (
                  <button
                    type="button"
                    onClick={() => {
                      setSearchQuery('');
                      searchInputRef.current?.focus();
                    }}
                    className="absolute right-2 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 p-0.5"
                    title="Clear search"
                  >
                    <Icon icon="carbon:close" className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>

              {/* Badges list */}
              <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto pr-1">
                {isLoadingGroups && normalizedGroups.length === 0 ? (
                  <div className="w-full py-2.5 px-3 rounded-lg border border-dashed border-zinc-200 dark:border-zinc-800 text-center">
                    <p className="text-xs text-zinc-500 dark:text-zinc-400">Loading existing manga…</p>
                  </div>
                ) : filteredGroups.length > 0 ? (
                  filteredGroups.map((grp) => {
                    const isSelected =
                      groupName.trim().toLowerCase() === grp.title.toLowerCase();
                    return (
                      <button
                        key={grp.id || grp.title}
                        type="button"
                        onClick={() => handleSelectGroup(grp)}
                        className={`inline-flex items-center space-x-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-all cursor-pointer ${
                          isSelected
                            ? 'bg-indigo-600 text-white shadow-xs'
                            : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-200 dark:hover:bg-zinc-700'
                        }`}
                        title={grp.title}
                      >
                        <Icon
                          icon={isSelected ? 'carbon:checkmark' : 'carbon:tag'}
                          className="h-3 w-3 shrink-0"
                        />
                        <span className="truncate max-w-[200px]">{grp.title}</span>
                        {grp.count !== undefined && (
                          <span className={`text-[10px] rounded px-1 py-0.2 ${
                            isSelected ? 'bg-indigo-700 text-indigo-100' : 'bg-zinc-200 dark:bg-zinc-700 text-zinc-500 dark:text-zinc-400'
                          }`}>
                            {grp.count}
                          </span>
                        )}
                      </button>
                    );
                  })
                ) : searchQuery.trim() ? (
                  <div className="w-full py-2.5 px-3 rounded-lg border border-dashed border-zinc-200 dark:border-zinc-800 text-center">
                    <p className="text-xs text-zinc-500 dark:text-zinc-400">
                      {isLoadingGroups
                        ? `Searching for "${searchQuery}"…`
                        : `No existing manga matching "${searchQuery}"`}
                    </p>
                    <button
                      type="button"
                      onClick={() => {
                        handleNameChange(searchQuery.trim());
                        setSearchQuery('');
                        inputRef.current?.focus();
                      }}
                      className="mt-1.5 inline-flex items-center space-x-1 text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer"
                    >
                      <Icon icon="carbon:add-alt" className="h-3.5 w-3.5" />
                      <span>Use &ldquo;{searchQuery.trim()}&rdquo; as new group name</span>
                    </button>
                  </div>
                ) : (
                  <div className="w-full py-2.5 px-3 rounded-lg border border-dashed border-zinc-200 dark:border-zinc-800 text-center">
                    <p className="text-xs text-zinc-500 dark:text-zinc-400">
                      No existing manga groups found
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Option for Ungrouped */}
          {allowUngrouped && (
            <div className="pt-1">
              <button
                type="button"
                onClick={() => {
                  setGroupName('');
                  setSelectedGroupId(null);
                }}
                className={`inline-flex items-center space-x-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-all ${
                  isCurrentUngrouped
                    ? 'border-indigo-400 dark:border-indigo-600 bg-indigo-50/50 dark:bg-indigo-950/30 text-indigo-700 dark:text-indigo-300'
                    : 'border-dashed border-zinc-200 dark:border-zinc-700 text-zinc-500 dark:text-zinc-400 hover:border-zinc-400 dark:hover:border-zinc-500'
                }`}
              >
                <Icon icon="carbon:folder-off" className="h-3 w-3" />
                <span>Leave Ungrouped (General Gallery)</span>
              </button>
            </div>
          )}

          {/* Action buttons */}
          <div className="flex items-center justify-end space-x-2 pt-3 border-t border-zinc-100 dark:border-zinc-800">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="rounded-xl px-4 py-2 text-xs font-semibold text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              aria-busy={isSubmitting}
              className="inline-flex items-center space-x-1.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white px-5 py-2 text-xs font-semibold shadow-md shadow-indigo-600/20 transition-all active:scale-[0.98]"
            >
              <Icon
                icon={
                  isSubmitting
                    ? 'carbon:renew'
                    : actionLabel
                    ? 'carbon:checkmark'
                    : 'carbon:translate'
                }
                className={`h-4 w-4 ${isSubmitting ? 'animate-spin' : ''}`}
              />
              <span>
                {isSubmitting
                  ? `${actionLabel || 'Adding to batch'}…`
                  : isCurrentUngrouped
                  ? actionLabel
                    ? `${actionLabel} (Ungrouped)`
                    : 'Translate Pages'
                  : isExistingSelected
                  ? actionLabel
                    ? `${actionLabel} to "${shortName}"`
                    : `Translate to "${shortName}"`
                  : actionLabel
                  ? `Create & ${actionLabel} "${shortName}"`
                  : `Create & Translate "${shortName}"`}
              </span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
