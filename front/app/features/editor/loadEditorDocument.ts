import type { EditableTextBlock, FinishedImage } from "@/types";
import { apiUrl } from "@/utils/api";
import { estimateFitFontSize, normalizeBlockDimensions } from "./geometry";
import { parseEditableTextBlocks } from "./serialization";

type BackgroundKind = "inpainted" | "input" | "result";

type LoadedBackground = {
  url: string;
  kind: BackgroundKind;
  image: HTMLImageElement;
};

export type LoadedEditorDocument = {
  blocks: EditableTextBlock[];
  background: LoadedBackground | null;
  imageSize: { width: number; height: number };
  backgroundError: string | null;
  backgroundNotice: string | null;
  cleanup: () => void;
};

function loadDecodedImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = async () => {
      try {
        await img.decode();
        resolve(img);
      } catch (error) {
        reject(error);
      }
    };
    img.onerror = () => reject(new Error(`Could not load image: ${url}`));
    img.src = url;
  });
}

const starterBlock = (): EditableTextBlock => ({
  id: "bubble_0",
  x: 100,
  y: 100,
  width: 260,
  height: 140,
  cover_background: false,
  translation: "Click to edit translation text",
  original_text: "",
  font_size: 26,
  font_family: "'Comic Neue', cursive, sans-serif",
  fg_color: [0, 0, 0],
  bg_color: [255, 255, 255],
  stroke_width: 3.5,
  angle: 0,
  direction: "h",
  alignment: "center",
  line_spacing: 1.15,
  letter_spacing: 0,
  bold: false,
  italic: false,
});

export async function loadEditorDocument(
  image: FinishedImage,
  {
    decodeImage = loadDecodedImage,
    fetcher = fetch,
  }: {
    decodeImage?: (url: string) => Promise<HTMLImageElement>;
    fetcher?: typeof fetch;
  } = {},
): Promise<LoadedEditorDocument> {
  const objectUrls: string[] = [];
  const candidates: { url: string; kind: BackgroundKind }[] = [];
  const seenUrls = new Set<string>();
  const addCandidate = (url: string | null | undefined, kind: BackgroundKind) => {
    if (url && !seenUrls.has(url)) {
      seenUrls.add(url);
      candidates.push({ url, kind });
    }
  };
  const addBlobCandidate = (value: Blob | null | undefined, kind: BackgroundKind) => {
    if (value) {
      const url = URL.createObjectURL(value);
      objectUrls.push(url);
      addCandidate(url, kind);
    }
  };

  // Prefer the clean layer, then try the original and final image as fallbacks.
  addCandidate(
    typeof image.inpaintedUrl === "string" ? apiUrl(image.inpaintedUrl) : image.inpaintedUrl,
    "inpainted",
  );
  if (image.folder) addCandidate(apiUrl(`/api/result/${image.folder}/inpainted.jpg`), "inpainted");
  if (typeof image.inputUrl === "string") addCandidate(apiUrl(image.inputUrl), "input");
  else addBlobCandidate(image.inputUrl, "input");
  if (typeof image.result === "string") addCandidate(apiUrl(image.result), "result");
  else addBlobCandidate(image.result, "result");

  const loadBackground = async (): Promise<LoadedBackground | null> => {
    for (const candidate of candidates) {
      try {
        const loadedImage = await decodeImage(candidate.url);
        return { ...candidate, image: loadedImage };
      } catch {
        // Try the next available source.
      }
    }
    return null;
  };
  const backgroundPromise = loadBackground();

  // Load text regions independently so a slow image does not delay metadata.
  let loadedBlocks: EditableTextBlock[] = [];
  let textRegionsNotice: string | null = null;
  const textRegionsUrl = typeof image.textRegionsUrl === "string"
    ? apiUrl(image.textRegionsUrl)
    : image.folder
      ? apiUrl(`/api/result/${image.folder}/text_regions.json`)
      : null;
  if (textRegionsUrl) {
    try {
      const response = await fetcher(textRegionsUrl);
      if (response.ok) {
        const data = await response.json();
        if (Array.isArray(data) && data.length > 0) loadedBlocks = parseEditableTextBlocks(data);
      } else {
        textRegionsNotice = `Saved text regions could not be loaded (${response.status}); added a starter bubble.`;
      }
    } catch (error) {
      console.warn("Could not fetch text_regions.json:", error);
      textRegionsNotice = "Saved text regions could not be loaded; added a starter bubble.";
    }
  } else {
    textRegionsNotice = "No saved text regions found; added a starter bubble.";
  }

  if (loadedBlocks.length === 0) loadedBlocks = [starterBlock()];

  const background = await backgroundPromise;
  const imageWidth = background?.image.naturalWidth || 1000;
  const imageHeight = background?.image.naturalHeight || 1400;
  const blocks = loadedBlocks.map((block) => {
    if (block.review_required || block.group_members?.length) return block;
    const layout = block.layout_bounds || block;
    const normalized = normalizeBlockDimensions(
      {
        x: layout.x,
        y: layout.y,
        width: layout.width,
        height: layout.height,
        direction: block.direction,
        translation: block.translation,
      },
      imageWidth,
      imageHeight,
    );
    const fontSize = estimateFitFontSize(
      block.translation,
      normalized.width,
      normalized.height,
      block.font_size,
      block.line_spacing,
    );
    return {
      ...block,
      x: normalized.x,
      y: normalized.y,
      width: normalized.width,
      height: normalized.height,
      font_size: fontSize,
      layout_bounds: normalized,
    };
  });

  const backgroundError = background
    ? null
    : "Page artwork could not be loaded from the available sources.";
  const fallbackNotice = background?.kind === "input"
    ? "Using the original page as a fallback; existing lettering may still be present."
    : background?.kind === "result"
      ? "Using the final result as a fallback; existing lettering may be duplicated."
      : null;
  const backgroundNotice = [fallbackNotice, textRegionsNotice].filter(Boolean).join(" ") || null;

  return {
    blocks,
    background,
    imageSize: { width: imageWidth, height: imageHeight },
    backgroundError,
    backgroundNotice,
    cleanup: () => objectUrls.forEach((url) => URL.revokeObjectURL(url)),
  };
}
