import assert from 'node:assert/strict';
import { isArchiveFile, extractMangaTitleFromFilename, naturalCompare, extractArchiveImages } from './zipUtils';

// 1. isArchiveFile
assert.equal(isArchiveFile({ name: 'chapter1.cbz' }), true);
assert.equal(isArchiveFile({ name: 'archive.ZIP' }), true);
assert.equal(isArchiveFile({ name: 'image.png' }), false);
assert.equal(isArchiveFile({ name: 'image.jpg', type: 'application/zip' }), true);
assert.equal(isArchiveFile({ name: 'image.jpg', type: 'application/vnd.comicbook+zip' }), true);
assert.equal(isArchiveFile({ name: 'document.pdf' }), false);

// 2. extractMangaTitleFromFilename
assert.equal(extractMangaTitleFromFilename('Berserk Vol 01.cbz'), 'Berserk Vol 01');
assert.equal(extractMangaTitleFromFilename('Chainsaw Man Ch 12.ZIP'), 'Chainsaw Man Ch 12');
assert.equal(extractMangaTitleFromFilename('Plain Manga'), 'Plain Manga');
assert.equal(extractMangaTitleFromFilename('downloads/One Piece 100.cbz'), 'One Piece 100');
assert.equal(extractMangaTitleFromFilename('C:\\Comics\\Solo Leveling.zip'), 'Solo Leveling');
assert.equal(extractMangaTitleFromFilename('  My Manga .cbz '), 'My Manga');
assert.equal(extractMangaTitleFromFilename('   Dragon Ball Vol 01 .cbz   '), 'Dragon Ball Vol 01');
assert.equal(extractMangaTitleFromFilename('   Naruto Ch 500.zip  '), 'Naruto Ch 500');

// 3. naturalCompare
const unsorted = ['page_10.png', 'page_2.png', 'page_1.png'];
unsorted.sort(naturalCompare);
assert.deepEqual(unsorted, ['page_1.png', 'page_2.png', 'page_10.png']);

// 4. extractArchiveImages with Stored and Deflate formats
async function runArchiveTests() {
  // Helper to create a minimal ZIP with Stored entries
  function buildTestZip(entries: { name: string; content: Uint8Array }[]): Uint8Array {
    const parts: Uint8Array[] = [];
    const cdEntries: Uint8Array[] = [];
    let offset = 0;

    for (const entry of entries) {
      const nameBytes = new TextEncoder().encode(entry.name);
      const localHeader = new Uint8Array(30 + nameBytes.length);
      const lView = new DataView(localHeader.buffer);

      lView.setUint32(0, 0x04034b50, true); // signature
      lView.setUint16(4, 20, true); // version needed
      lView.setUint16(6, 0, true); // flags
      lView.setUint16(8, 0, true); // method 0 (stored)
      lView.setUint16(10, 0, true); // time
      lView.setUint16(12, 0, true); // date
      lView.setUint32(14, 0, true); // crc32 dummy
      lView.setUint32(18, entry.content.length, true); // comp size
      lView.setUint32(22, entry.content.length, true); // uncomp size
      lView.setUint16(26, nameBytes.length, true); // fn len
      lView.setUint16(28, 0, true); // extra len
      localHeader.set(nameBytes, 30);

      parts.push(localHeader);
      parts.push(entry.content);

      // Central directory header
      const cdHeader = new Uint8Array(46 + nameBytes.length);
      const cdView = new DataView(cdHeader.buffer);
      cdView.setUint32(0, 0x02014b50, true);
      cdView.setUint16(4, 20, true);
      cdView.setUint16(6, 20, true);
      cdView.setUint16(8, 0, true);
      cdView.setUint16(10, 0, true); // stored
      cdView.setUint16(12, 0, true);
      cdView.setUint16(14, 0, true);
      cdView.setUint32(16, 0, true);
      cdView.setUint32(20, entry.content.length, true);
      cdView.setUint32(24, entry.content.length, true);
      cdView.setUint16(28, nameBytes.length, true);
      cdView.setUint16(30, 0, true);
      cdView.setUint16(32, 0, true);
      cdView.setUint16(34, 0, true);
      cdView.setUint16(36, 0, true);
      cdView.setUint32(38, 0, true);
      cdView.setUint32(42, offset, true); // local offset
      cdHeader.set(nameBytes, 46);
      cdEntries.push(cdHeader);

      offset += localHeader.length + entry.content.length;
    }

    const cdOffset = offset;
    let cdSize = 0;
    for (const cd of cdEntries) {
      parts.push(cd);
      cdSize += cd.length;
    }

    // End of central directory record
    const eocd = new Uint8Array(22);
    const eocdView = new DataView(eocd.buffer);
    eocdView.setUint32(0, 0x06054b50, true);
    eocdView.setUint16(4, 0, true);
    eocdView.setUint16(6, 0, true);
    eocdView.setUint16(8, entries.length, true);
    eocdView.setUint16(10, entries.length, true);
    eocdView.setUint32(12, cdSize, true);
    eocdView.setUint32(16, cdOffset, true);
    eocdView.setUint16(20, 0, true);
    parts.push(eocd);

    const totalLen = parts.reduce((acc, p) => acc + p.length, 0);
    const out = new Uint8Array(totalLen);
    let cur = 0;
    for (const p of parts) {
      out.set(p, cur);
      cur += p.length;
    }
    return out;
  }

  const zipBytes = buildTestZip([
    { name: 'ComicInfo.xml', content: new TextEncoder().encode('<ComicInfo/>') },
    { name: '__MACOSX/._01.png', content: new Uint8Array([1, 2, 3]) },
    { name: 'ch1/page_02.jpg', content: new Uint8Array([0xff, 0xd8, 0xff, 0xe0]) },
    { name: 'ch1/page_01.png', content: new Uint8Array([0x89, 0x50, 0x4e, 0x47]) },
  ]);

  const file = new File([zipBytes as unknown as BlobPart], 'test.cbz', { type: 'application/vnd.comicbook+zip' });
  const extracted = await extractArchiveImages(file);

  assert.equal(extracted.length, 2, 'Should extract exactly 2 images, ignoring ComicInfo and __MACOSX');
  assert.equal(extracted[0].name, 'page_01.png');
  assert.equal(extracted[0].sourcePath, 'ch1/page_01.png');
  assert.equal(extracted[0].type, 'image/png');
  assert.equal(extracted[1].name, 'page_02.jpg');
  assert.equal(extracted[1].type, 'image/jpeg');

  // Test empty zip error
  const emptyZip = buildTestZip([
    { name: 'notes.txt', content: new TextEncoder().encode('hello') },
  ]);
  const emptyFile = new File([emptyZip as unknown as BlobPart], 'empty.zip', { type: 'application/zip' });
  await assert.rejects(
    () => extractArchiveImages(emptyFile),
    /No supported images/
  );

  console.log('All zipUtils tests passed successfully!');
}

runArchiveTests().catch((err) => {
  console.error('zipUtils test failed:', err);
  process.exit(1);
});
