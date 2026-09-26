import React from 'react';
import type { GalleryMangaGroup, MangaStatusFilter } from '@/utils/resultGallery';
import { GalleryBulkSelectionBar } from './GalleryBulkSelectionBar';
import { GalleryNoResultsState } from './GalleryStatusStates';
import { MangaCard } from './MangaCard';
import { MangaRowGroup } from './MangaRowGroup';

type MangaCardProps = React.ComponentProps<typeof MangaCard>;
type MangaRowProps = React.ComponentProps<typeof MangaRowGroup>;

interface BulkSelection {
  selectedCount: number;
  onMove: () => void;
  onDelete?: () => void;
  onRerender?: () => void;
  onClear: () => void;
}

interface GalleryLibraryViewProps {
  viewMode: string;
  filteredGroups: GalleryMangaGroup[];
  visibleGroups: GalleryMangaGroup[];
  searchQuery: string;
  statusFilter: MangaStatusFilter;
  onResetFilters: () => void;
  bulkSelection: BulkSelection | null;
  getCardProps: (group: GalleryMangaGroup, index: number) => MangaCardProps;
  getRowState: (group: GalleryMangaGroup) => MangaRowProps['state'];
  rowGroupActions: MangaRowProps['actions'];
}

export function GalleryLibraryView({
  viewMode,
  filteredGroups,
  visibleGroups,
  searchQuery,
  statusFilter,
  onResetFilters,
  bulkSelection,
  getCardProps,
  getRowState,
  rowGroupActions,
}: GalleryLibraryViewProps) {
  return (
    <>
      {viewMode === 'cards' && (
        filteredGroups.length === 0 ? (
          <GalleryNoResultsState
            searchQuery={searchQuery}
            statusFilter={statusFilter}
            onResetFilters={onResetFilters}
          />
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4 sm:gap-5">
            {visibleGroups.map((group, index) => (
              <MangaCard key={group.id} {...getCardProps(group, index)} />
            ))}
          </div>
        )
      )}

      {viewMode === 'rows' && (
        <div className="space-y-6">
          {bulkSelection && (
            <GalleryBulkSelectionBar
              selectedCount={bulkSelection.selectedCount}
              onMove={bulkSelection.onMove}
              onDelete={bulkSelection.onDelete}
              onRerender={bulkSelection.onRerender}
              onClear={bulkSelection.onClear}
            />
          )}

          {filteredGroups.length === 0 ? (
            <GalleryNoResultsState
              searchQuery={searchQuery}
              statusFilter={statusFilter}
              onResetFilters={onResetFilters}
            />
          ) : (
            visibleGroups.map((group) => (
              <MangaRowGroup
                key={group.id}
                group={group}
                state={getRowState(group)}
                actions={rowGroupActions}
              />
            ))
          )}
        </div>
      )}
    </>
  );
}
