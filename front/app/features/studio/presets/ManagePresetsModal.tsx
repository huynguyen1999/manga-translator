import React, { useState } from "react";
import { Icon } from "@iconify/react";
import { useModalEscape } from "@/utils/useModalEscape";
import type { TranslationPreset } from "./presetTypes";

export interface ManagePresetsModalProps {
  isOpen: boolean;
  onClose: () => void;
  presets: TranslationPreset[];
  activePresetId: string | null;
  onSelect: (presetId: string) => void;
  onSetDefault: (presetId: string) => Promise<void>;
  onDelete: (presetId: string) => Promise<void>;
  onOverwrite: () => Promise<void>;
  onRename?: (presetId: string, name: string, description?: string) => Promise<TranslationPreset | void>;
}

export const ManagePresetsModal: React.FC<ManagePresetsModalProps> = ({
  isOpen,
  onClose,
  presets,
  activePresetId,
  onSelect,
  onSetDefault,
  onDelete,
  onOverwrite,
  onRename,
}) => {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useModalEscape(isOpen, onClose);
  if (!isOpen) return null;

  const startEditing = (preset: TranslationPreset) => {
    setEditingId(preset.id);
    setEditName(preset.name);
    setEditDesc(preset.description || "");
    setConfirmDeleteId(null);
    setError(null);
  };

  const cancelEditing = () => {
    setEditingId(null);
    setEditName("");
    setEditDesc("");
  };

  const handleSaveEdit = async (presetId: string) => {
    const clean = editName.trim();
    if (!clean) {
      setError("Preset name cannot be empty");
      return;
    }
    if (clean.length > 100) {
      setError("Preset name must be 100 characters or fewer");
      return;
    }
    setSavingEdit(true);
    setError(null);
    try {
      if (onRename) {
        await onRename(presetId, clean, editDesc.trim());
      }
      setEditingId(null);
    } catch (err: any) {
      setError(err.message || "Failed to update preset");
    } finally {
      setSavingEdit(false);
    }
  };

  const handleSetDefault = async (presetId: string) => {
    setBusyId(presetId);
    setError(null);
    try {
      await onSetDefault(presetId);
    } catch (err: any) {
      setError(err.message || "Failed to set default preset");
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (presetId: string) => {
    setBusyId(presetId);
    setError(null);
    try {
      await onDelete(presetId);
      setConfirmDeleteId(null);
    } catch (err: any) {
      setError(err.message || "Failed to delete preset");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-xs">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-center justify-between border-b border-zinc-100 p-4 dark:border-zinc-800">
          <div className="flex items-center space-x-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600 dark:bg-indigo-950/60 dark:text-indigo-400">
              <Icon icon="carbon:settings-adjust" className="h-4 w-4" />
            </div>
            <h3 className="text-sm font-bold text-zinc-900 dark:text-zinc-100">
              Manage Translation Presets
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          >
            <Icon icon="carbon:close" className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {error && (
            <div className="rounded-lg bg-rose-50 p-2.5 text-xs text-rose-600 dark:bg-rose-950/40 dark:text-rose-400">
              {error}
            </div>
          )}

          {presets.length === 0 ? (
            <div className="py-12 text-center text-zinc-500 dark:text-zinc-400">
              <Icon icon="carbon:bookmark" className="mx-auto mb-2 h-8 w-8 text-zinc-300 dark:text-zinc-600" />
              <p className="text-xs font-medium">No saved presets yet</p>
              <p className="text-[11px] text-zinc-400">
                Adjust options in Web Studio and click &quot;Save As...&quot; to save your favorite workflows.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {presets.map((preset) => {
                const isActive = preset.id === activePresetId;
                const isDeleting = confirmDeleteId === preset.id;
                const isEditing = editingId === preset.id;
                const isBusy = busyId === preset.id;

                return (
                  <div
                    key={preset.id}
                    className={`flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-lg border p-3 transition-colors ${
                      isActive
                        ? "border-indigo-200 bg-indigo-50/40 dark:border-indigo-800/60 dark:bg-indigo-950/20"
                        : "border-zinc-200 bg-zinc-50/50 dark:border-zinc-800 dark:bg-zinc-800/30"
                    }`}
                  >
                    {isEditing ? (
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          handleSaveEdit(preset.id);
                        }}
                        className="flex-1 space-y-2"
                      >
                        <div className="flex flex-col sm:flex-row gap-2">
                          <input
                            type="text"
                            autoFocus
                            required
                            maxLength={100}
                            value={editName}
                            onChange={(e) => setEditName(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Escape") cancelEditing();
                            }}
                            placeholder="Preset name"
                            className="flex-1 rounded-md border border-indigo-300 bg-white px-2.5 py-1 text-xs font-semibold text-zinc-900 focus:border-indigo-500 focus:outline-hidden focus:ring-1 focus:ring-indigo-500 dark:border-indigo-600 dark:bg-zinc-800 dark:text-zinc-100"
                          />
                          <input
                            type="text"
                            maxLength={500}
                            value={editDesc}
                            onChange={(e) => setEditDesc(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Escape") cancelEditing();
                            }}
                            placeholder="Description (optional)"
                            className="flex-1 rounded-md border border-zinc-300 bg-white px-2.5 py-1 text-xs text-zinc-700 focus:border-indigo-500 focus:outline-hidden focus:ring-1 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
                          />
                        </div>
                        <div className="flex items-center space-x-1.5 justify-end">
                          <button
                            type="submit"
                            disabled={savingEdit || !editName.trim()}
                            className="flex items-center space-x-1 rounded-md bg-indigo-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                          >
                            <Icon
                              icon={savingEdit ? "carbon:circle-dash" : "carbon:checkmark"}
                              className={`h-3 w-3 ${savingEdit ? "animate-spin" : ""}`}
                            />
                            <span>{savingEdit ? "Saving..." : "Save"}</span>
                          </button>
                          <button
                            type="button"
                            disabled={savingEdit}
                            onClick={cancelEditing}
                            className="rounded-md border border-zinc-200 px-2.5 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                          >
                            Cancel
                          </button>
                        </div>
                      </form>
                    ) : (
                      <>
                        <div className="min-w-0 flex-1 space-y-1">
                          <div className="flex items-center space-x-2">
                            <span
                              className="font-semibold text-xs text-zinc-900 dark:text-zinc-100 truncate cursor-pointer hover:text-indigo-600 dark:hover:text-indigo-400"
                              title="Click to rename preset"
                              onClick={() => startEditing(preset)}
                            >
                              {preset.name}
                            </span>
                            <button
                              type="button"
                              onClick={() => startEditing(preset)}
                              className="rounded p-0.5 text-zinc-400 hover:bg-zinc-200/60 hover:text-zinc-700 dark:hover:bg-zinc-700 dark:hover:text-zinc-200"
                              title="Edit preset title"
                            >
                              <Icon icon="carbon:edit" className="h-3 w-3" />
                            </button>
                            {preset.isDefault && (
                              <span className="inline-flex items-center rounded-full bg-indigo-100 px-2 py-0.5 text-[10px] font-bold text-indigo-700 dark:bg-indigo-900/60 dark:text-indigo-300">
                                Default
                              </span>
                            )}
                            {isActive && (
                              <span className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-900/60 dark:text-emerald-300">
                                Active
                              </span>
                            )}
                          </div>
                          {preset.description ? (
                            <p className="text-[11px] text-zinc-500 dark:text-zinc-400 line-clamp-1">
                              {preset.description}
                            </p>
                          ) : null}
                        </div>

                        <div className="flex items-center space-x-1 self-end sm:self-center">
                          {!isActive && (
                            <button
                              type="button"
                              onClick={() => {
                                onSelect(preset.id);
                                onClose();
                              }}
                              className="rounded-md border border-zinc-200 bg-white px-2.5 py-1 text-[11px] font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
                            >
                              Apply
                            </button>
                          )}

                          <button
                            type="button"
                            onClick={() => startEditing(preset)}
                            className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-indigo-600 dark:hover:bg-zinc-800 dark:hover:text-indigo-400"
                            title="Edit preset title & description"
                          >
                            <Icon icon="carbon:edit" className="h-4 w-4" />
                          </button>

                          {!preset.isDefault && (
                            <button
                              type="button"
                              disabled={isBusy}
                              onClick={() => handleSetDefault(preset.id)}
                              className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-amber-500 dark:hover:bg-zinc-800"
                              title="Set as default preset"
                            >
                              <Icon icon="carbon:star" className="h-4 w-4" />
                            </button>
                          )}

                          {isDeleting ? (
                            <div className="flex items-center space-x-1">
                              <button
                                type="button"
                                disabled={isBusy}
                                onClick={() => handleDelete(preset.id)}
                                className="rounded-md bg-rose-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-rose-700"
                              >
                                Confirm
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmDeleteId(null)}
                                className="rounded-md border border-zinc-200 px-2 py-1 text-[11px] text-zinc-600 dark:border-zinc-700 dark:text-zinc-300"
                              >
                                Cancel
                              </button>
                            </div>
                          ) : (
                            <button
                              type="button"
                              onClick={() => setConfirmDeleteId(preset.id)}
                              className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-rose-500 dark:hover:bg-zinc-800"
                              title="Delete preset"
                            >
                              <Icon icon="carbon:trash-can" className="h-4 w-4" />
                            </button>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end border-t border-zinc-100 p-3 dark:border-zinc-800">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-zinc-200 px-3.5 py-1.5 text-xs font-medium text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
