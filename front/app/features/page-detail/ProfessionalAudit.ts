export interface ProfessionalAuditRegion {
  id?: string;
  source?: string;
  draft?: string;
  final?: string;
  confidence?: number;
  review_reasons?: string[];
}

export interface ProfessionalStoryAnalysis {
  start_page?: number;
  end_page?: number;
  confidence?: number;
  summary?: string;
  characters?: unknown;
  relationships?: unknown;
  honorific_policy?: unknown;
  language_features?: unknown;
  localization_conventions?: unknown;
  glossary?: unknown;
  voice_notes?: unknown;
  continuity?: unknown;
  ambiguities?: unknown;
}

export interface ProfessionalTranslationAudit {
  regions?: ProfessionalAuditRegion[];
  storyIndex?: number | null;
  analysis?: { stories?: ProfessionalStoryAnalysis[] };
}
