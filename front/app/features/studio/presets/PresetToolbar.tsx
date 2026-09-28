import React, { useState } from "react";
import { Icon } from "@iconify/react";
import type { TranslationPreset } from "./presetTypes";
import { SavePresetModal } from "./SavePresetModal";
import { ManagePresetsModal } from "./ManagePresetsModal";

export interface PresetToolbarProps {
  presets: TranslationPreset[];
  activePreset: TranslationPreset | null;
  activePresetId: string | null;
  isModified: boolean;
  loading: boolean;
  onSelectPreset: (presetId: string | null) => void;
  onSaveAsNew: (name: string, description: string, isDefault: boolean) => Promise<TranslationPreset>;
  onUpdateActive: () => Promise<TranslationPreset | null>;
  onRevertActive: () => void;
  onSetDefault: (presetId: string) => Promise<void>;
  onDeletePreset: (presetId: string) => Promise<void>;
  onRenamePreset?: (presetId: string, name: string, description?: string) => Promise<TranslationPreset | void>;
}

export const PresetToolbar: React.FC<PresetToolbarProps> = ({
  presets,
  activePreset,
  activePresetId,
  isModified,
  loading,
  onSelectPreset,
  onSaveAsNew,
  onUpdateActive,
  onRevertActive,
  onSetDefault,
  onDeletePreset,
  onRenamePreset,
}) => {
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [showManageModal, setShowManageModal] = useState(false);
  const [updating, setUpdating] = useState(false);

  const handleUpdate = async () => {
    setUpdating(true);
    try {
      await onUpdateActive();
    } finally {
      setUpdating(false);
    }
  };

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center space-x-1.5">
          <div className="flex h-5 w-5 items-center justify-center rounded-md bg-amber-50 text-amber-600 dark:bg-amber-950/60 dark:text-amber-400">
            <Icon icon="carbon:bookmark" className="h-3.5 w-3.5" />
          </div>
          <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
            Preset:
          </span>
        </div>

        <select
          value={activePresetId || ""}
          onChange={(e) => onSelectPreset(e.target.value || null)}
          className="rounded-lg border border-zinc-200/80 bg-white px-2.5 py-1 text-xs font-medium text-zinc-800 shadow-2xs transition-colors hover:border-zinc-300 focus:border-indigo-500 focus:outline-hidden dark:border-zinc-700/80 dark:bg-zinc-800 dark:text-zinc-200"
          title="Select a translation preset"
        >
          <option value="">
            {activePresetId ? "Custom / Unsaved" : "Select Preset..."}
          </option>
          {presets.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} {p.isDefault ? "★ (Default)" : ""}
            </option>
          ))}
        </select>

        {activePreset && isModified && (
          <div className="flex items-center space-x-1">
            <span
              className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
              title="Current options differ from the saved preset"
            >
              Modified
            </span>
            <button
              type="button"
              disabled={updating}
              onClick={handleUpdate}
              className="flex items-center space-x-1 rounded-md bg-amber-500 px-2 py-1 text-[11px] font-medium text-white hover:bg-amber-600 disabled:opacity-50"
              title="Save current options into this preset"
            >
              <Icon icon={updating ? "carbon:circle-dash" : "carbon:save"} className={`h-3 w-3 ${updating ? "animate-spin" : ""}`} />
              <span>Update</span>
            </button>
            <button
              type="button"
              onClick={onRevertActive}
              className="flex items-center space-x-1 rounded-md border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
              title="Revert options back to preset"
            >
              <Icon icon="carbon:undo" className="h-3 w-3" />
              <span>Revert</span>
            </button>
          </div>
        )}

        <button
          type="button"
          onClick={() => setShowSaveModal(true)}
          className="flex items-center space-x-1 rounded-md border border-zinc-200/80 bg-white px-2.5 py-1 text-xs font-medium text-zinc-700 shadow-2xs hover:bg-zinc-50 dark:border-zinc-700/80 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700/80"
          title="Save current options as a new named preset"
        >
          <Icon icon="carbon:save" className="h-3.5 w-3.5 text-zinc-500" />
          <span>Save As...</span>
        </button>

        <button
          type="button"
          onClick={() => setShowManageModal(true)}
          className="flex items-center space-x-1 rounded-md p-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          title="Manage saved presets"
        >
          <Icon icon="carbon:settings-adjust" className="h-3.5 w-3.5" />
        </button>
      </div>

      <SavePresetModal
        isOpen={showSaveModal}
        onClose={() => setShowSaveModal(false)}
        onSave={async (name, desc, isDef) => {
          await onSaveAsNew(name, desc, isDef);
        }}
      />

      <ManagePresetsModal
        isOpen={showManageModal}
        onClose={() => setShowManageModal(false)}
        presets={presets}
        activePresetId={activePresetId}
        onSelect={(id) => onSelectPreset(id)}
        onSetDefault={onSetDefault}
        onDelete={onDeletePreset}
        onRename={onRenamePreset}
        onOverwrite={async () => {
          await onUpdateActive();
        }}
      />
    </>
  );
};
