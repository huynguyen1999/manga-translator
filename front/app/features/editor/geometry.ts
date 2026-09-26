export function getFitZoom(
  containerWidth: number,
  containerHeight: number,
  imageWidth: number,
  imageHeight: number,
  padding = 64,
): number {
  const availableWidth = Math.max(1, containerWidth - padding);
  const availableHeight = Math.max(1, containerHeight - padding);
  const scale = Math.min(availableWidth / imageWidth, availableHeight / imageHeight);

  return Number.isFinite(scale) && scale > 0 ? Math.min(1, Math.max(0.05, scale)) : 1;
}

export interface RawBlockDimensions {
  x: number;
  y: number;
  width: number;
  height: number;
  direction?: "h" | "v";
  translation?: string;
}

export function wrapTextLines(
  text: string,
  measureText: (value: string) => number,
  maxWidth: number,
): string[] {
  const width = Math.max(1, maxWidth);
  const lines: string[] = [];

  const splitWord = (word: string): string[] => {
    const chars = Array.from(word);
    const parts: string[] = [];
    let start = 0;

    while (start < chars.length) {
      let bestLength = 0;
      for (let length = 1; start + length <= chars.length; length += 1) {
        const hasMore = start + length < chars.length;
        const candidate = chars.slice(start, start + length).join("") + (hasMore ? "-" : "");
        if (measureText(candidate) <= width || bestLength === 0) {
          bestLength = length;
        } else {
          break;
        }
      }
      const hasMore = start + bestLength < chars.length;
      parts.push(chars.slice(start, start + bestLength).join("") + (hasMore ? "-" : ""));
      start += bestLength;
    }
    return parts;
  };

  for (const rawLine of text.replace(/\r\n?/g, "\n").split("\n")) {
    const words = rawLine.trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) {
      lines.push("");
      continue;
    }

    let current = "";
    for (const word of words) {
      if (measureText(word) > width) {
        if (current) {
          lines.push(current);
          current = "";
        }
        const pieces = splitWord(word);
        for (const piece of pieces.slice(0, -1)) lines.push(piece);
        current = pieces[pieces.length - 1] || "";
        continue;
      }

      const candidate = current ? `${current} ${word}` : word;
      if (!current || measureText(candidate) <= width) {
        current = candidate;
      } else {
        lines.push(current);
        current = word;
      }
    }
    if (current) lines.push(current);
  }

  return lines.length > 0 ? lines : [""];
}

/**
 * Manga dialogue in Japanese is predominantly written vertically in tall, narrow strips
 * (e.g. width: 39px, height: 120px for 1 vertical text line).
 * When translating to a horizontal language (e.g. English), placing text in a 39px-wide strip
 * causes words to violently break character-by-character.
 *
 * This function expands narrow vertical boxes to a natural horizontal speech bubble shape
 * centered on the original box, clamped to the image boundary.
 */
export function normalizeBlockDimensions(
  block: RawBlockDimensions,
  imageWidth: number,
  imageHeight: number,
): { x: number; y: number; width: number; height: number } {
  let { x, y, width, height, direction } = block;

  if (direction !== "v") {
    // Horizontal text in a tall, narrow vertical box (aspect ratio width/height < 0.8)
    // or box is narrower than a typical readable English bubble (e.g. width < 140px)
    if (width < 140 && height >= 50) {
      const targetRatio = 0.85; // Natural manga speech bubble width-to-height ratio
      const desiredWidth = Math.max(140, Math.round(height * targetRatio));
      if (desiredWidth > width) {
        const delta = desiredWidth - width;
        const newX = Math.max(0, Math.min(imageWidth - desiredWidth, Math.round(x - delta / 2)));
        x = newX;
        width = Math.min(desiredWidth, imageWidth - x);
      }
    }

    // Ensure minimal dimensions for horizontal text
    width = Math.max(60, width);
    height = Math.max(30, height);
  } else {
    width = Math.max(30, width);
    height = Math.max(60, height);
  }

  // Ensure within image bounds
  x = Math.max(0, Math.min(Math.max(0, imageWidth - width), x));
  y = Math.max(0, Math.min(Math.max(0, imageHeight - height), y));

  return { x, y, width, height };
}

/**
 * Computes an estimated optimal font size for horizontal English dialogue
 * given the bubble dimensions and text content, avoiding overflowing or clipping.
 */
export function estimateFitFontSize(
  text: string,
  width: number,
  height: number,
  initialFontSize: number = 24,
  lineSpacing: number = 1.15,
): number {
  if (!text || !text.trim()) return Math.min(Math.max(initialFontSize, 12), 32);

  const cleanText = text.trim();
  // Comics speech bubbles are typically oval / rounded.
  // Using 80% of width and 82% of height ensures text sits comfortably inside
  // the bubble without clipping near the rounded borders.
  const availW = Math.max(20, Math.floor(width * 0.80));
  const availH = Math.max(20, Math.floor(height * 0.82));

  // Start with initial font size or a reasonable upper bound
  let fontSize = Math.min(initialFontSize > 0 ? initialFontSize : 24, 42);

  // In Comic Neue / comic fonts, average character width is ~0.56 * fontSize.
  const measureAt = (size: number) => (value: string) => value.length * size * 0.54;
  while (fontSize > 11) {
    const lineHeight = fontSize * lineSpacing;
    const lines = wrapTextLines(cleanText, measureAt(fontSize), availW);
    const totalH = lines.length * lineHeight;
    if (totalH <= availH) {
      break;
    }
    fontSize -= 1;
  }

  return Math.max(11, fontSize);
}

