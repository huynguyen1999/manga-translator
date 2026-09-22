/**
 * Fast client-side color detection using HTML5 Canvas.
 * Accurately detects if an uploaded manga image is already colored
 * (e.g. color covers, color spreads, watercolor illustrations)
 * while distinguishing them from aged, yellowed, or black-and-white scans.
 */
export async function detectIsImageColored(file: File | Blob): Promise<boolean> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const img = new Image();

    img.onload = () => {
      try {
        const maxDim = 128;
        let w = img.naturalWidth || img.width;
        let h = img.naturalHeight || img.height;
        if (w <= 0 || h <= 0) {
          URL.revokeObjectURL(url);
          return resolve(false);
        }

        if (w > maxDim || h > maxDim) {
          if (w >= h) {
            h = Math.max(1, Math.round((h * maxDim) / w));
            w = maxDim;
          } else {
            w = Math.max(1, Math.round((w * maxDim) / h));
            h = maxDim;
          }
        }

        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        if (!ctx) {
          URL.revokeObjectURL(url);
          return resolve(false);
        }

        ctx.drawImage(img, 0, 0, w, h);
        URL.revokeObjectURL(url);
        const imgData = ctx.getImageData(0, 0, w, h);
        const data = imgData.data;

        let totalValid = 0;
        let saturatedCount = 0;
        let totalGrayDiff = 0;
        const satValues: number[] = [];

        for (let i = 0; i < data.length; i += 4) {
          const r = data[i] / 255;
          const g = data[i + 1] / 255;
          const b = data[i + 2] / 255;

          const max = Math.max(r, g, b);
          const min = Math.min(r, g, b);
          const delta = max - min;
          const v = max;
          const s = max === 0 ? 0 : delta / max;

          // Grayscale distance
          const gray = 0.299 * r + 0.587 * g + 0.114 * b;
          const diff = (Math.abs(r - gray) + Math.abs(g - gray) + Math.abs(b - gray)) / 3;
          totalGrayDiff += diff * 255;

          // Ignore black lines (v < 0.12) and bright white paper (v > 0.96 && s < 0.08)
          if (v < 0.12 || (v > 0.96 && s < 0.08)) {
            continue;
          }

          totalValid++;
          satValues.push(s);
          if (s > 0.22) {
            saturatedCount++;
          }
        }

        URL.revokeObjectURL(url);

        const totalPixels = w * h;
        const avgGrayDist = totalPixels > 0 ? totalGrayDiff / totalPixels : 0;
        const colorRatio = totalValid > 0 ? saturatedCount / totalValid : 0;

        satValues.sort((a, b) => a - b);
        const p95Sat =
          satValues.length > 0 ? satValues[Math.floor(satValues.length * 0.95)] : 0;

        // Signal 1: High global color difference
        if (avgGrayDist >= 31.0) {
          return resolve(true);
        }

        // Signal 2: Significant cluster of saturated pixels (color characters on white paper)
        if (colorRatio >= 0.015 && p95Sat >= 0.28) {
          return resolve(true);
        }

        // Signal 3: Moderate color presence
        if (avgGrayDist >= 8.0 && p95Sat >= 0.22 && colorRatio >= 0.008) {
          return resolve(true);
        }

        return resolve(false);
      } catch {
        URL.revokeObjectURL(url);
        return resolve(false);
      }
    };

    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(false);
    };

    img.src = url;
  });
}
