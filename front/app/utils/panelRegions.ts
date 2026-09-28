import { linesFrom, numberValue } from "./textRegions";

type RawRegion = Record<string, unknown>;

export interface DetectedPanelRegion {
  id: string;
  index: number;
  order: number;
  xyxy: [number, number, number, number];
  polygons: Array<Array<[number, number]>>;
  imageSize?: { width: number; height: number };
  confidence?: number | null;
}

export const parsePanelDetections = (data: unknown): DetectedPanelRegion[] => {
  const records = Array.isArray(data)
    ? data
    : data && typeof data === "object"
    ? ((data as RawRegion).panels ?? (data as RawRegion).detections ?? (data as RawRegion).regions)
    : null;
  if (!Array.isArray(records)) return [];

  return records.flatMap((raw, idx) => {
    if (!raw || typeof raw !== "object") return [];
    const item = raw as RawRegion;
    let polygons = linesFrom(item.polygons);
    if (polygons.length === 0) polygons = linesFrom(item.polygon);
    let xyxy: [number, number, number, number] = [0, 0, 0, 0];
    if (Array.isArray(item.xyxy) && item.xyxy.length >= 4) {
      const x1 = numberValue(item.xyxy[0]) ?? 0;
      const y1 = numberValue(item.xyxy[1]) ?? 0;
      const x2 = numberValue(item.xyxy[2]) ?? 0;
      const y2 = numberValue(item.xyxy[3]) ?? 0;
      xyxy = [x1, y1, x2, y2];
      if (polygons.length === 0 && x2 > x1 && y2 > y1) {
        polygons = [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]];
      }
    } else if (Array.isArray(item.bbox) && item.bbox.length >= 4) {
      const x1 = numberValue(item.bbox[0]) ?? 0;
      const y1 = numberValue(item.bbox[1]) ?? 0;
      const x2 = numberValue(item.bbox[2]) ?? 0;
      const y2 = numberValue(item.bbox[3]) ?? 0;
      xyxy = [x1, y1, x2, y2];
      if (polygons.length === 0 && x2 > x1 && y2 > y1) {
        polygons = [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]];
      }
    } else if (polygons.length > 0 && polygons[0].length > 0) {
      const xs = polygons.flatMap((p) => p.map(([x]) => x));
      const ys = polygons.flatMap((p) => p.map(([, y]) => y));
      xyxy = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    }

    if (xyxy[2] <= xyxy[0] || xyxy[3] <= xyxy[1]) return [];
    const imageWidth = Array.isArray(item.image_size) ? numberValue(item.image_size[0]) : null;
    const imageHeight = Array.isArray(item.image_size) ? numberValue(item.image_size[1]) : null;
    const savedIndex = numberValue(item.index) ?? idx;
    const order = numberValue(item.order ?? item.reading_order) ?? (savedIndex + 1);

    return [{
      id: String(item.id ?? item.panel_id ?? `panel_${order}`),
      index: savedIndex,
      order,
      xyxy,
      polygons,
      ...(imageWidth !== null && imageHeight !== null && imageWidth > 0 && imageHeight > 0
        ? { imageSize: { width: imageWidth, height: imageHeight } }
        : {}),
      confidence: numberValue(item.confidence ?? item.prob),
    }];
  });
};

export const normalizePanelBoxes = (panels: DetectedPanelRegion[]): Array<[number, number, number, number]> => {
  return panels.map((p) => p.xyxy);
};

