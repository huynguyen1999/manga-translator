import { useCallback, useEffect, useMemo, useState } from "react";
import type { TranslationSettings } from "@/types";
import type {
  CreatePresetPayload,
  TranslationPreset,
} from "./presetTypes";
import {
  createPreset,
  deletePreset,
  fetchPresets,
  setDefaultPreset,
  updatePreset,
} from "@/utils/presetApi";
import { isSettingsModified, sanitizeSettingsForPreset } from "./presetUtils";
import { applyPresetSettings, type PresetSetters } from "./applyPresetSettings";

export interface UsePresetsOptions {
  getCurrentSettings: () => TranslationSettings;
  setters?: PresetSetters;
  applySettings?: (settings: Partial<TranslationSettings>) => void;
  autoHydrateDefault?: boolean;
}

export const usePresets = ({
  getCurrentSettings,
  setters,
  applySettings,
  autoHydrateDefault = true,
}: UsePresetsOptions) => {
  const [presets, setPresets] = useState<TranslationPreset[]>([]);
  const [activePresetId, setActivePresetId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback(
    (settings: Partial<TranslationSettings>) => {
      if (applySettings) {
        applySettings(settings);
      } else if (setters) {
        applyPresetSettings(settings, setters);
      }
    },
    [applySettings, setters],
  );

  const activePreset = useMemo(
    () => presets.find((p) => p.id === activePresetId) || null,
    [presets, activePresetId],
  );

  const isModified = useMemo(() => {
    if (!activePreset) return false;
    const current = getCurrentSettings();
    return isSettingsModified(current, activePreset.settings);
  }, [activePreset, getCurrentSettings]);

  const loadPresets = useCallback(
    async (autoApplyDefault = false) => {
      setLoading(true);
      setError(null);
      try {
        const list = await fetchPresets();
        setPresets(list);
        if (autoApplyDefault && !activePresetId) {
          const defaultPreset = list.find((p) => p.isDefault);
          if (defaultPreset) {
            apply(defaultPreset.settings);
            setActivePresetId(defaultPreset.id);
          }
        }
      } catch (err: any) {
        setError(err.message || "Failed to load presets");
      } finally {
        setLoading(false);
      }
    },
    [activePresetId, apply],
  );

  useEffect(() => {
    loadPresets(autoHydrateDefault);
  }, []);

  const selectPreset = useCallback(
    (presetId: string | null) => {
      if (!presetId) {
        setActivePresetId(null);
        return;
      }
      const target = presets.find((p) => p.id === presetId);
      if (target) {
        apply(target.settings);
        setActivePresetId(target.id);
      }
    },
    [presets, apply],
  );

  const saveCurrentAsNew = useCallback(
    async (name: string, description = "", isDefault = false): Promise<TranslationPreset> => {
      const current = getCurrentSettings();
      const sanitized = sanitizeSettingsForPreset(current);
      const payload: CreatePresetPayload = {
        name,
        description,
        settings: sanitized,
        isDefault,
      };
      const created = await createPreset(payload);
      setPresets((prev) => {
        const next = isDefault
          ? prev.map((p) => ({ ...p, isDefault: false }))
          : [...prev];
        return [created, ...next];
      });
      setActivePresetId(created.id);
      return created;
    },
    [getCurrentSettings],
  );

  const updateActivePreset = useCallback(async (): Promise<TranslationPreset | null> => {
    if (!activePreset) return null;
    const current = getCurrentSettings();
    const sanitized = sanitizeSettingsForPreset(current);
    const updated = await updatePreset(activePreset.id, {
      settings: sanitized,
    });
    setPresets((prev) =>
      prev.map((p) => (p.id === updated.id ? updated : p)),
    );
    return updated;
  }, [activePreset, getCurrentSettings]);

  const revertActivePreset = useCallback(() => {
    if (!activePreset) return;
    apply(activePreset.settings);
  }, [activePreset, apply]);

  const deletePresetById = useCallback(
    async (presetId: string) => {
      await deletePreset(presetId);
      setPresets((prev) => prev.filter((p) => p.id !== presetId));
      if (activePresetId === presetId) {
        setActivePresetId(null);
      }
    },
    [activePresetId],
  );

  const setDefaultById = useCallback(async (presetId: string) => {
    const updated = await setDefaultPreset(presetId);
    setPresets((prev) =>
      prev.map((p) => ({
        ...p,
        isDefault: p.id === updated.id,
      })),
    );
  }, []);

  const renamePreset = useCallback(
    async (
      presetId: string,
      name: string,
      description?: string,
    ): Promise<TranslationPreset> => {
      const updated = await updatePreset(presetId, {
        name,
        description,
      });
      setPresets((prev) =>
        prev.map((p) => (p.id === updated.id ? updated : p)),
      );
      return updated;
    },
    [],
  );

  return {
    presets,
    activePreset,
    activePresetId,
    isModified,
    loading,
    error,
    loadPresets,
    selectPreset,
    saveCurrentAsNew,
    updateActivePreset,
    revertActivePreset,
    deletePresetById,
    setDefaultById,
    renamePreset,
    clearActivePreset: () => setActivePresetId(null),
  };
};
