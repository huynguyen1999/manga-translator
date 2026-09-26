import type { EditableTextBlock } from "@/types";
import { wrapTextLines } from "./geometry";

export function rgbToHex(rgb: [number, number, number]): string {
  const [r, g, b] = rgb;
  const toHex = (n: number) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function approximateTextWidth(value: string, fontSize: number, letterSpacing: number): number {
  return Array.from(value).length * fontSize * 0.54 + Math.max(0, Array.from(value).length - 1) * Math.max(0, letterSpacing);
}

export function getHorizontalLines(
  block: EditableTextBlock,
  text = block.translation,
  width = block.width,
): string[] {
  return wrapTextLines(
    text,
    (value) => approximateTextWidth(value, block.font_size || 24, block.letter_spacing || 0),
    Math.max(1, width - 10),
  );
}

export async function renderMangaEditorCanvas(
  backgroundImage: HTMLImageElement | null,
  imageSize: { width: number; height: number },
  blocks: EditableTextBlock[],
): Promise<HTMLCanvasElement> {
  const canvas = document.createElement("canvas");
  canvas.width = imageSize.width;
  canvas.height = imageSize.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Could not get 2D context");

  // 1. Draw the already decoded background. Never export a silent blank page.
  const background = backgroundImage;
  if (!background || !background.complete || !background.naturalWidth) {
    throw new Error("Page artwork is not loaded.");
  }
  ctx.drawImage(background, 0, 0, imageSize.width, imageSize.height);

  // 2. Render all text segments. Linked lobes share style but own geometry/text.
  const renderBlocks = blocks.flatMap((block) =>
    block.layout_segments?.length
      ? block.layout_segments.map((segment) => ({ ...block, ...segment, translation: segment.text, font_size: segment.font_size ?? block.font_size, layout_segments: undefined, linkedLobe: true }))
      : [{ ...block, linkedLobe: false }],
  );
  for (const block of renderBlocks) {
    if (block.linkedLobe && !block.rendered_png) continue;
    if (!block.translation || !block.translation.trim()) continue;

    ctx.save();

    const centerX = block.x + block.width / 2;
    const centerY = block.y + block.height / 2;

    ctx.translate(centerX, centerY);
    if (block.angle) {
      ctx.rotate((block.angle * Math.PI) / 180);
    }
    ctx.translate(-centerX, -centerY);
    ctx.beginPath();
    ctx.rect(block.x, block.y, block.width, block.height);
    ctx.clip();
    if (block.cover_background) {
      ctx.fillStyle = "#fff";
      ctx.fillRect(block.x, block.y, block.width, block.height);
    }

    if (block.rendered_png) {
      const lettering = new Image();
      lettering.src = `data:image/png;base64,${block.rendered_png}`;
      await lettering.decode();
      ctx.drawImage(lettering, block.x, block.y, block.width, block.height);
      ctx.restore();
      continue;
    }

    const fontSize = block.font_size || 24;
    const fontFamily = block.font_family || "Comic Neue, cursive, sans-serif";
    const fontStyle = `${block.italic ? "italic " : ""}${block.bold ? "bold " : ""}${fontSize}px ${fontFamily}`;
    ctx.font = fontStyle;
    ctx.textBaseline = "top";
    ctx.textAlign = block.alignment || "center";

    const textColor = rgbToHex(block.fg_color);
    const strokeColor = rgbToHex(block.bg_color);
    const strokeWidth = block.stroke_width || 0;
    const letterSpacing = block.letter_spacing || 0;
    const measureTextWidth = (value: string) =>
      ctx.measureText(value).width + Math.max(0, Array.from(value).length - 1) * letterSpacing;
    const paintText = (value: string, x: number, y: number, alignment: CanvasTextAlign) => {
      if (!value) return;
      const chars = Array.from(value);
      if (letterSpacing === 0 || chars.length === 1) {
        ctx.textAlign = alignment;
        if (strokeWidth > 0) ctx.strokeText(value, x, y);
        ctx.fillText(value, x, y);
        return;
      }

      const width = measureTextWidth(value);
      let cursor = alignment === "center" ? x - width / 2 : alignment === "right" ? x - width : x;
      ctx.textAlign = "left";
      for (const char of chars) {
        if (strokeWidth > 0) ctx.strokeText(char, cursor, y);
        ctx.fillText(char, cursor, y);
        cursor += ctx.measureText(char).width + letterSpacing;
      }
    };

    const lineHeight = fontSize * (block.line_spacing ?? 1.15);
    let alignX = block.x + block.width / 2;
    if (block.alignment === "left") alignX = block.x + 8;
    if (block.alignment === "right") alignX = block.x + block.width - 8;

    if (strokeWidth > 0) {
      ctx.strokeStyle = strokeColor;
      ctx.lineWidth = strokeWidth * 2;
      ctx.lineJoin = "round";
      ctx.miterLimit = 2;
    }
    ctx.fillStyle = textColor;

    if (block.direction === "v") {
      const columns: string[] = [];
      const maxHeight = Math.max(fontSize, block.height - 10);
      for (const rawColumn of block.translation.replace(/\r\n?/g, "\n").split("\n")) {
        let column = "";
        for (const char of Array.from(rawColumn)) {
          if (column && (column.length + 1) * lineHeight - letterSpacing > maxHeight) {
            columns.push(column);
            column = "";
          }
          column += char;
        }
        columns.push(column);
      }

      let columnX = block.x + block.width - fontSize / 2 - 4;
      const startY = block.y + 5;
      for (const column of columns) {
        let y = startY;
        for (const char of Array.from(column)) {
          paintText(char, columnX, y, "center");
          y += lineHeight + letterSpacing;
        }
        columnX -= lineHeight;
      }
    } else {
      if (block.positioned_lines?.length) {
        for (const line of block.positioned_lines) paintText(line.text, line.x, line.y, "left");
      } else {
        const lines = getHorizontalLines(block);
        const totalTextHeight = lines.length * lineHeight;
        let startY = block.y + Math.max(0, (block.height - totalTextHeight) / 2);
        for (const line of lines) {
          paintText(line, alignX, startY, block.alignment || "center");
          startY += lineHeight;
        }
      }
    }

    ctx.restore();
  }

  return canvas;
}
