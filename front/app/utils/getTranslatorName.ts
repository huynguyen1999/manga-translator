import type { TranslatorKey } from "@/types";

const TRANSLATOR_NAMES: Record<TranslatorKey, string> = {
  deepseek: "DeepSeek (API)",
  gemini: "Gemini (Google API)",
  openai: "ChatGPT (OpenAI API)",
  groq: "Groq (API)",
  openrouter: "OpenRouter (API)",
  sugoi: "Sugoi V4.0 (Offline Manga)",
  custom_openai: "Custom OpenAI / Ollama (Local)",
  sakura: "Sakura (Local LLM / API)",
  deepl: "DeepL (API)",
  youdao: "Youdao (API)",
  baidu: "Baidu (API)",
  caiyun: "Caiyun (API)",
  none: "No Text (Inpaint Only)",
};

const TRANSLATOR_GROUPS: Record<TranslatorKey, string> = {
  deepseek: "Cloud AI (LLM)",
  gemini: "Cloud AI (LLM)",
  openai: "Cloud AI (LLM)",
  groq: "Cloud AI (LLM)",
  openrouter: "Cloud AI (LLM)",
  sugoi: "Offline / Local Models",
  custom_openai: "Offline / Local Models",
  sakura: "Offline / Local Models",
  deepl: "Cloud Translation Services",
  youdao: "Cloud Translation Services",
  baidu: "Cloud Translation Services",
  caiyun: "Cloud Translation Services",
  none: "Special",
};

export function getTranslatorName(key: TranslatorKey): string {
  return TRANSLATOR_NAMES[key] || (key ? key[0].toUpperCase() + key.slice(1) : "");
}

export function getTranslatorGroup(key: TranslatorKey): string {
  return TRANSLATOR_GROUPS[key] || "Other";
}

