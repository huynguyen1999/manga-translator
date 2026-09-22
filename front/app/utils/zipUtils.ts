/**
 * Utility functions for detecting and extracting images from .cbz and .zip archives.
 * Pure Web APIs (DataView, DecompressionStream) without external dependencies.
 */

const ARCHIVE_EXTENSIONS = /\.(cbz|zip)$/i;

const ARCHIVE_MIME_TYPES = new Set([
  'application/zip',
  'application/x-cbz',
  'application/x-zip-compressed',
  'application/vnd.comicbook+zip',
]);

const SUPPORTED_IMAGE_EXTENSIONS: Record<string, string> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.bmp': 'image/bmp',
};

export type ExtractedImageFile = File & { sourcePath: string };

export const ARCHIVE_ACCEPT_STRING =
  '.zip,.cbz,application/zip,application/x-zip-compressed,application/vnd.comicbook+zip,application/x-cbz';

export const SUPPORTED_UPLOAD_ACCEPT =
  'image/*,.cbz,.zip';

/**
 * Check if a file is a .cbz or .zip comic archive.
 */
export function isArchiveFile(file: { name: string; type?: string }): boolean {
  if (ARCHIVE_EXTENSIONS.test(file.name)) {
    return true;
  }
  if (file.type && ARCHIVE_MIME_TYPES.has(file.type.toLowerCase())) {
    return true;
  }
  return false;
}

/**
 * Extract a suggested manga title from an archive filename by stripping extension.
 */
export function extractMangaTitleFromFilename(fileName: string): string {
  const baseName = (fileName.split(/[/\\]/).pop() || fileName).trim();
  const stripped = baseName.replace(ARCHIVE_EXTENSIONS, '').trim();
  return stripped || baseName.trim();
}

/**
 * Compare strings using natural alphanumeric sorting (e.g. page_2 before page_10).
 */
export function naturalCompare(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
}

/**
 * Extracts and decompresses supported images from a .cbz or .zip file in natural order.
 */
export async function extractArchiveImages(file: File | (Blob & { name?: string })): Promise<ExtractedImageFile[]> {
  const totalLength = file.size;

  if (totalLength < 22) {
    throw new Error('Archive file is too small to be a valid zip.');
  }

  // Locate End of Central Directory Record (EOCDR) from the end (reading only the tail of the file)
  const maxSearch = Math.min(totalLength - 22, 65557);
  const tailReadSize = maxSearch + 22;
  const tailBuffer = await file.slice(totalLength - tailReadSize, totalLength).arrayBuffer();
  const tailView = new DataView(tailBuffer);

  let eocdRelOffset = -1;
  for (let i = tailBuffer.byteLength - 22; i >= 0; i--) {
    if (tailView.getUint32(i, true) === 0x06054b50) {
      eocdRelOffset = i;
      break;
    }
  }

  if (eocdRelOffset === -1) {
    throw new Error('Could not find ZIP central directory header. Invalid or corrupted archive.');
  }

  const totalEntries = tailView.getUint16(eocdRelOffset + 10, true);
  const cdSize = tailView.getUint32(eocdRelOffset + 12, true);
  const cdOffset = tailView.getUint32(eocdRelOffset + 16, true);

  if (cdOffset >= totalLength || cdOffset + cdSize > totalLength) {
    throw new Error('Malformed ZIP: central directory offset out of bounds.');
  }

  // Read only the Central Directory bytes
  const cdBuffer = await file.slice(cdOffset, cdOffset + cdSize).arrayBuffer();
  const cdView = new DataView(cdBuffer);
  const textDecoder = new TextDecoder('utf-8');
  let cur = 0;

  interface EntryMeta {
    filename: string;
    compressionMethod: number;
    compressedSize: number;
    localHeaderOffset: number;
  }

  const entries: EntryMeta[] = [];

  for (let i = 0; i < totalEntries && cur + 46 <= cdBuffer.byteLength; i++) {
    if (cdView.getUint32(cur, true) !== 0x02014b50) {
      break;
    }

    const compressionMethod = cdView.getUint16(cur + 10, true);
    const compressedSize = cdView.getUint32(cur + 20, true);
    const fnLen = cdView.getUint16(cur + 28, true);
    const extraLen = cdView.getUint16(cur + 30, true);
    const commentLen = cdView.getUint16(cur + 32, true);
    const localHeaderOffset = cdView.getUint32(cur + 42, true);

    if (cur + 46 + fnLen > cdBuffer.byteLength) break;

    const rawFilenameBytes = new Uint8Array(cdBuffer, cur + 46, fnLen);
    const entryPath = textDecoder.decode(rawFilenameBytes);

    cur += 46 + fnLen + extraLen + commentLen;

    // Filter out directories, Mac metadata, and hidden files
    if (entryPath.endsWith('/') || entryPath.endsWith('\\')) continue;
    const parts = entryPath.split(/[/\\]/);
    if (parts.some((p) => p.startsWith('.') || p === '__MACOSX')) continue;

    const lower = entryPath.toLowerCase();
    const extMatch = Object.keys(SUPPORTED_IMAGE_EXTENSIONS).find((ext) => lower.endsWith(ext));
    if (!extMatch) continue;

    entries.push({
      filename: entryPath,
      compressionMethod,
      compressedSize,
      localHeaderOffset,
    });
  }

  if (entries.length === 0) {
    throw new Error('No supported images (PNG, JPEG, WEBP, BMP) found in archive.');
  }

  // Sort entries naturally by their archive path
  entries.sort((a, b) => naturalCompare(a.filename, b.filename));

  const resultFiles: ExtractedImageFile[] = [];

  for (let idx = 0; idx < entries.length; idx++) {
    const entry = entries[idx];
    const localOffset = entry.localHeaderOffset;

    if (localOffset + 30 > totalLength) {
      throw new Error(`Malformed ZIP local header for entry ${entry.filename}`);
    }

    const localHeaderBuf = await file.slice(localOffset, localOffset + 30).arrayBuffer();
    const localHeaderView = new DataView(localHeaderBuf);

    if (localHeaderView.getUint32(0, true) !== 0x04034b50) {
      throw new Error(`Invalid local header signature for entry ${entry.filename}`);
    }

    const localFnLen = localHeaderView.getUint16(26, true);
    const localExtraLen = localHeaderView.getUint16(28, true);
    const dataStart = localOffset + 30 + localFnLen + localExtraLen;

    if (dataStart + entry.compressedSize > totalLength) {
      throw new Error(`Truncated data for entry ${entry.filename}`);
    }

    const compressedBlob = file.slice(dataStart, dataStart + entry.compressedSize);
    let decompressedBlob: Blob;

    if (entry.compressionMethod === 0) {
      // Stored (no compression)
      decompressedBlob = compressedBlob;
    } else if (entry.compressionMethod === 8) {
      // Deflate
      const ds = new DecompressionStream('deflate-raw');
      const writer = ds.writable.getWriter();
      const compressedBuffer = await compressedBlob.arrayBuffer();
      writer.write(new Uint8Array(compressedBuffer));
      writer.close();

      const response = new Response(ds.readable);
      const decompressedBuffer = await response.arrayBuffer();
      decompressedBlob = new Blob([decompressedBuffer]);
    } else {
      throw new Error(`Unsupported ZIP compression method (${entry.compressionMethod}) for entry ${entry.filename}`);
    }

    // Keep the basename as the visible label; sourcePath disambiguates duplicates.
    const rawName = entry.filename.split(/[/\\]/).pop() || `page_${idx + 1}.png`;
    const lowerExt = rawName.slice(rawName.lastIndexOf('.')).toLowerCase();
    const mimeType = SUPPORTED_IMAGE_EXTENSIONS[lowerExt] || 'image/png';

    const extracted = new File([decompressedBlob], rawName, { type: mimeType }) as ExtractedImageFile;
    Object.defineProperty(extracted, 'sourcePath', { value: entry.filename, enumerable: true });
    resultFiles.push(extracted);
  }

  return resultFiles;
}
