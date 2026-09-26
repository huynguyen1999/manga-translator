import type { EditableTextBlock } from "@/types";

type RawRegion = Record<string, unknown>;

const numberValue = (value: unknown): number | null => {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
};

const pointsFrom = (value: unknown): Array<[number, number]> => {
  if (!Array.isArray(value)) return [];
  const x = numberValue(value[0]);
  const y = numberValue(value[1]);
  if (x !== null && y !== null) return [[x, y]];
  return value.flatMap(pointsFrom);
};

const linesFrom = (value: unknown): Array<Array<[number, number]>> => {
  if (!Array.isArray(value) || value.length === 0) return [];
  const rawLines = Array.isArray(value[0]) && Array.isArray(value[0][0]) ? value : [value];
  return rawLines.flatMap((rawLine) => {
    if (!Array.isArray(rawLine)) return [];
    const points: Array<[number, number]> = rawLine.flatMap((point) => {
      if (!Array.isArray(point)) return [];
      const x = numberValue(point[0]);
      const y = numberValue(point[1]);
      return x !== null && y !== null ? [[x, y]] : [];
    });
    return points.length >= 3 ? [points] : [];
  });
};

const boundsFrom = (item: RawRegion): { x: number; y: number; width: number; height: number } | null => {
  const direct = [item.x, item.y, item.width, item.height].map(numberValue);
  if (direct.every((value) => value !== null) && direct[2]! > 0 && direct[3]! > 0) {
    return { x: direct[0]!, y: direct[1]!, width: direct[2]!, height: direct[3]! };
  }

  for (const candidate of [item.xywh, item.bbox, item.bounds, item.layout_bounds]) {
    if (Array.isArray(candidate)) {
      const values = candidate.slice(0, 4).map(numberValue);
      if (values.length === 4 && values.every((value) => value !== null) && values[2]! > 0 && values[3]! > 0) {
        return { x: values[0]!, y: values[1]!, width: values[2]!, height: values[3]! };
      }
    } else if (candidate && typeof candidate === "object") {
      const values = ["x", "y", "width", "height"].map((key) => numberValue((candidate as RawRegion)[key]));
      if (values.every((value) => value !== null) && values[2]! > 0 && values[3]! > 0) {
        return { x: values[0]!, y: values[1]!, width: values[2]!, height: values[3]! };
      }
    }
  }

  const points = pointsFrom(item.pts ?? item.points ?? item.lines);
  if (points.length === 0) return null;
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  const x = Math.min(...xs);
  const y = Math.min(...ys);
  const width = Math.max(...xs) - x;
  const height = Math.max(...ys) - y;
  return width > 0 && height > 0 ? { x, y, width, height } : null;
};

export const parseTextRegions = (data: unknown): EditableTextBlock[] => {
  const records = Array.isArray(data)
    ? data
    : data && typeof data === "object"
    ? ((data as RawRegion).textRegions ?? (data as RawRegion).text_regions ?? (data as RawRegion).regions)
    : null;
  if (!Array.isArray(records)) return [];

  return records.flatMap((raw, idx) => {
    if (!raw || typeof raw !== "object") return [];
    const item = raw as RawRegion;
    const bounds = boundsFrom(item);
    if (!bounds) return [];
    return [{
      ...bounds,
      id: String(item.id ?? `text_region_${idx + 1}`),
      bubble_safe_shape: item.bubble_safe_shape && typeof item.bubble_safe_shape === "object"
        ? item.bubble_safe_shape as EditableTextBlock["bubble_safe_shape"] : null,
      group_members: Array.isArray(item.group_members) ? item.group_members.filter((value): value is string => typeof value === "string") : undefined,
      review_required: item.review_required === true,
      review_reason: typeof item.review_reason === "string" ? item.review_reason : null,
      layout_segments: Array.isArray(item.layout_segments)
        ? item.layout_segments.flatMap((rawSegment) => {
            if (!rawSegment || typeof rawSegment !== "object") return [];
            const segment = rawSegment as RawRegion;
            const segmentBounds = boundsFrom(segment);
            return segmentBounds
              ? [{
                  ...segmentBounds,
                  text: String(segment.text ?? ""),
                  font_size: numberValue(segment.font_size) ?? undefined,
                  rendered_png: typeof segment.rendered_png === "string" ? segment.rendered_png : null,
                  positioned_lines: Array.isArray(segment.positioned_lines)
                    ? segment.positioned_lines.flatMap((rawLine) => {
                        if (!rawLine || typeof rawLine !== "object") return [];
                        const line = rawLine as RawRegion;
                        return [{ text: String(line.text ?? ""), x: numberValue(line.x) ?? 0,
                                  y: numberValue(line.y) ?? 0 }];
                      }) : undefined,
                }]
              : [];
          })
        : undefined,
      lines: linesFrom(item.lines),
      original_text: typeof item.original_text === "string" ? item.original_text : "",
      translation: String(item.translation ?? ""),
      confidence: numberValue(item.confidence ?? item.prob),
      prob: numberValue(item.prob ?? item.confidence),
      font_size: numberValue(item.font_size) ?? 24,
      font_family: typeof item.font_family === "string" ? item.font_family : "",
      fg_color: Array.isArray(item.fg_color) ? item.fg_color as [number, number, number] : [0, 0, 0],
      bg_color: Array.isArray(item.bg_color) ? item.bg_color as [number, number, number] : [255, 255, 255],
      stroke_width: numberValue(item.stroke_width) ?? 2,
      angle: numberValue(item.angle) ?? 0,
      direction: item.direction === "v" ? "v" : "h",
      alignment: item.alignment === "left" || item.alignment === "right" ? item.alignment : "center",
      line_spacing: numberValue(item.line_spacing) ?? 1,
      letter_spacing: numberValue(item.letter_spacing) ?? 1,
      bold: item.bold === true,
      italic: item.italic === true,
    } as EditableTextBlock];
  });
};

export const countOriginalTextRegions = (blocks: EditableTextBlock[]): number =>
  blocks.reduce((count, block) => count + (block.lines?.length ?? 0), 0);

export interface DetectedRegionLine {
  id: string;
  points: Array<[number, number]>;
  confidence?: number | null;
}

export interface DetectedBubbleRegion {
  id: string;
  polygons: Array<Array<[number, number]>>;
  imageSize?: { width: number; height: number };
  confidence?: number | null;
}

export const parseDetectionRegions = (data: unknown): DetectedRegionLine[] => {
  const records = Array.isArray(data)
    ? data
    : data && typeof data === "object"
    ? ((data as RawRegion).regions ?? (data as RawRegion).detections)
    : null;
  if (!Array.isArray(records)) return [];

  return records.flatMap((raw, idx) => {
    if (!raw || typeof raw !== "object") return [];
    const item = raw as RawRegion;
    const confidence = numberValue(item.confidence ?? item.prob);
    const savedIndex = numberValue(item.index);
    const lines = linesFrom(item.lines ?? item.pts ?? item.points);
    return lines.map((pts) => ({
      id: String(item.id ?? item.region_id ?? `detection_${(savedIndex ?? idx) + 1}`),
      points: pts,
      confidence: confidence != null ? confidence : null,
    }));
  });
};

export const parseBubbleDetections = (data: unknown): DetectedBubbleRegion[] => {
  const records = Array.isArray(data)
    ? data
    : data && typeof data === "object"
    ? ((data as RawRegion).detections ?? (data as RawRegion).regions)
    : null;
  if (!Array.isArray(records)) return [];

  return records.flatMap((raw, idx) => {
    if (!raw || typeof raw !== "object") return [];
    const item = raw as RawRegion;
    let polygons = linesFrom(item.polygons);
    if (polygons.length === 0) polygons = linesFrom(item.polygon);
    if (polygons.length === 0 && Array.isArray(item.xyxy) && item.xyxy.length >= 4) {
      const x1 = numberValue(item.xyxy[0]);
      const y1 = numberValue(item.xyxy[1]);
      const x2 = numberValue(item.xyxy[2]);
      const y2 = numberValue(item.xyxy[3]);
      if (x1 !== null && y1 !== null && x2 !== null && y2 !== null && x2 > x1 && y2 > y1) {
        polygons = [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]];
      }
    }
    if (polygons.length === 0) return [];
    const imageWidth = Array.isArray(item.image_size) ? numberValue(item.image_size[0]) : null;
    const imageHeight = Array.isArray(item.image_size) ? numberValue(item.image_size[1]) : null;
    const savedIndex = numberValue(item.index);
    return [{
      id: String(item.id ?? item.bubble_id ?? `speech_bubble_${(savedIndex ?? idx) + 1}`),
      polygons,
      ...(imageWidth !== null && imageHeight !== null && imageWidth > 0 && imageHeight > 0
        ? { imageSize: { width: imageWidth, height: imageHeight } }
        : {}),
      confidence: numberValue(item.confidence ?? item.prob),
    }];
  });
};
