import type { TranslationSettings } from "@/types";

export interface TranslationPreset {
  id: string;
  name: string;
  description: string;
  isDefault: boolean;
  settings: Partial<TranslationSettings>;
  createdAt: string;
  updatedAt: string;
}

export interface CreatePresetPayload {
  name: string;
  description?: string;
  settings: Partial<TranslationSettings>;
  isDefault?: boolean;
}

export interface UpdatePresetPayload {
  name?: string;
  description?: string;
  settings?: Partial<TranslationSettings>;
  isDefault?: boolean;
}
