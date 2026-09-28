import { apiUrl } from "./api";
import type {
  CreatePresetPayload,
  TranslationPreset,
  UpdatePresetPayload,
} from "@/features/studio/presets/presetTypes";

export const fetchPresets = async (): Promise<TranslationPreset[]> => {
  const response = await fetch(apiUrl("/api/presets"));
  if (!response.ok) {
    throw new Error(`Failed to fetch presets: ${response.statusText}`);
  }
  const data = await response.json();
  return Array.isArray(data.presets) ? data.presets : [];
};

export const createPreset = async (
  payload: CreatePresetPayload,
): Promise<TranslationPreset> => {
  const response = await fetch(apiUrl("/api/presets"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to create preset: ${response.statusText}`);
  }
  return response.json();
};

export const updatePreset = async (
  presetId: string,
  payload: UpdatePresetPayload,
): Promise<TranslationPreset> => {
  const response = await fetch(apiUrl(`/api/presets/${encodeURIComponent(presetId)}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to update preset: ${response.statusText}`);
  }
  return response.json();
};

export const deletePreset = async (presetId: string): Promise<void> => {
  const response = await fetch(apiUrl(`/api/presets/${encodeURIComponent(presetId)}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to delete preset: ${response.statusText}`);
  }
};

export const setDefaultPreset = async (presetId: string): Promise<TranslationPreset> => {
  const response = await fetch(apiUrl(`/api/presets/${encodeURIComponent(presetId)}/default`), {
    method: "POST",
  });
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to set default preset: ${response.statusText}`);
  }
  return response.json();
};
