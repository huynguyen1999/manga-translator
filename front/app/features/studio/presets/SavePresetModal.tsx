import React, { useState } from "react";
import { Icon } from "@iconify/react";

export interface SavePresetModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (name: string, description: string, isDefault: boolean) => Promise<void>;
}

export const SavePresetModal: React.FC<SavePresetModalProps> = ({
  isOpen,
  onClose,
  onSave,
}) => {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isDefault, setIsDefault] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanName = name.trim();
    if (!cleanName) {
      setError("Please enter a preset name");
      return;
    }
    if (cleanName.length > 100) {
      setError("Preset name must be 100 characters or fewer");
      return;
    }

    setSaving(true);
    setError(null);
    try {
      await onSave(cleanName, description.trim(), isDefault);
      setName("");
      setDescription("");
      setIsDefault(false);
      onClose();
    } catch (err: any) {
      setError(err.message || "Failed to save preset");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-xs">
      <div className="w-full max-w-md rounded-xl border border-zinc-200 bg-white p-5 shadow-xl dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-center justify-between border-b border-zinc-100 pb-3 dark:border-zinc-800">
          <div className="flex items-center space-x-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600 dark:bg-indigo-950/60 dark:text-indigo-400">
              <Icon icon="carbon:bookmark-add" className="h-4 w-4" />
            </div>
            <h3 className="text-sm font-bold text-zinc-900 dark:text-zinc-100">
              Save Current Settings as Preset
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

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          {error && (
            <div className="rounded-lg bg-rose-50 p-2.5 text-xs text-rose-600 dark:bg-rose-950/40 dark:text-rose-400">
              {error}
            </div>
          )}

          <div>
            <label className="mb-1 block text-xs font-semibold text-zinc-700 dark:text-zinc-300">
              Preset Name <span className="text-rose-500">*</span>
            </label>
            <input
              type="text"
              autoFocus
              required
              maxLength={100}
              placeholder="e.g., Clean Shonen Typeset, Color Manhwa HD"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-xs text-zinc-800 focus:border-indigo-500 focus:outline-hidden focus:ring-1 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-semibold text-zinc-700 dark:text-zinc-300">
              Description (Optional)
            </label>
            <textarea
              rows={2}
              maxLength={500}
              placeholder="Brief note about the workflow or target manga style..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-xs text-zinc-800 focus:border-indigo-500 focus:outline-hidden focus:ring-1 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100"
            />
          </div>

          <label className="flex cursor-pointer items-center space-x-2 text-xs text-zinc-600 dark:text-zinc-300">
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
              className="h-4 w-4 rounded border-zinc-300 text-indigo-600 focus:ring-indigo-500 dark:border-zinc-600"
            />
            <span>Set as default preset for Web Studio</span>
          </label>

          <div className="flex items-center justify-end space-x-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-zinc-200 px-3 py-1.5 text-xs font-medium text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !name.trim()}
              className="flex items-center space-x-1.5 rounded-lg bg-indigo-600 px-3.5 py-1.5 text-xs font-medium text-white shadow-xs hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? (
                <Icon icon="carbon:circle-dash" className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Icon icon="carbon:save" className="h-3.5 w-3.5" />
              )}
              <span>{saving ? "Saving..." : "Save Preset"}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
