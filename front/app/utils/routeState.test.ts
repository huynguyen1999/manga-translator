import assert from 'node:assert/strict';
import {
  parseAppPath,
  getLegacyRedirect,
  getNavigationOrigin,
  validatePriorRoute,
  buildReaderUrl,
  buildReaderIdUrl,
  buildPageViewUrl,
  buildPageEditUrl,
  buildMangaDetailUrl,
  buildMangaDetailIdUrl,
  buildSeriesDetailUrl,
  buildGalleryPageUrl,
  mangaIdForTitle,
  DEFAULT_GALLERY_PAGE_SIZE,
  GALLERY_PAGE_SIZE_OPTIONS,
} from './routeState';

console.log('Running routeState unit tests...');

// 1. parseAppPath tests
{
  const studio = parseAppPath('/studio');
  assert.equal(studio.view, 'studio');
  assert.equal(studio.overlay, 'none');

  const gallery = parseAppPath('/gallery');
  assert.equal(gallery.view, 'gallery');
  assert.equal(gallery.overlay, 'none');
  assert.equal(gallery.gallerySort, 'date-desc');
  assert.equal(DEFAULT_GALLERY_PAGE_SIZE, 25);
  assert.deepEqual(GALLERY_PAGE_SIZE_OPTIONS, [25, 50, 75, 100]);

  const pipeline = parseAppPath('/pipeline-lab');
  assert.equal(pipeline.view, 'pipeline');
  assert.equal(pipeline.overlay, 'none');

  const reader = parseAppPath('/read', '?manga=My%20Cool%20Manga');
  assert.equal(reader.view, 'gallery');
  assert.equal(reader.overlay, 'reader');
  assert.equal(reader.mangaTitle, 'My Cool Manga');

  const readerMangaId = '550e8400-e29b-41d4-a716-446655440000';
  const readerById = parseAppPath('/read', `?manga=${encodeURIComponent(readerMangaId)}`);
  assert.equal(readerById.mangaId, readerMangaId);

  const pageView = parseAppPath('/gallery/pages/result_123_456');
  assert.equal(pageView.view, 'gallery');
  assert.equal(pageView.overlay, 'viewer');
  assert.equal(pageView.folder, 'result_123_456');

  const pageEdit = parseAppPath('/gallery/pages/result_123_456/edit');
  assert.equal(pageEdit.view, 'gallery');
  assert.equal(pageEdit.overlay, 'editor');
  assert.equal(pageEdit.folder, 'result_123_456');

  // Manga Detail dedicated endpoint tests
  const mangaDetail = parseAppPath('/gallery/manga/Solo%20Leveling');
  assert.equal(mangaDetail.view, 'gallery');
  assert.equal(mangaDetail.overlay, 'none');
  assert.equal(mangaDetail.mangaTitle, 'Solo Leveling');

  const mangaDetailSpecial = parseAppPath('/gallery/manga/Manga%20%26%20Anime%20%231');
  assert.equal(mangaDetailSpecial.view, 'gallery');
  assert.equal(mangaDetailSpecial.overlay, 'none');
  assert.equal(mangaDetailSpecial.mangaTitle, 'Manga & Anime #1');

  const mangaId = mangaIdForTitle('Solo Leveling');
  const mangaById = parseAppPath(buildMangaDetailUrl('Solo Leveling'));
  assert.equal(mangaById.mangaId, mangaId);
  const reviewManga = parseAppPath(buildMangaDetailUrl('Solo Leveling', true).split('?')[0], '?review=pending');
  assert.equal(reviewManga.mangaId, mangaId);
  assert.equal(reviewManga.reviewOnly, true);
  const postgresMangaId = '550e8400-e29b-41d4-a716-446655440000';
  assert.equal(parseAppPath(buildMangaDetailIdUrl(postgresMangaId)).mangaId, postgresMangaId);

  const galleryPage = parseAppPath('/gallery', '?page=3');
  assert.equal(galleryPage.galleryPage, 3);
  assert.equal(parseAppPath('/gallery', '?page=0').galleryPage, 1);
  assert.equal(parseAppPath('/gallery').galleryPageSize, 25);
  assert.equal(parseAppPath('/gallery', '?pageSize=50').galleryPageSize, 50);
  assert.equal(parseAppPath('/gallery', '?search=Solo%20Leveling').gallerySearch, 'Solo Leveling');
  assert.equal(parseAppPath('/gallery', '?review=pending').reviewOnly, true);
  assert.equal(parseAppPath('/gallery', '?review=pending').galleryStatus, 'review');
  assert.equal(parseAppPath('/gallery', '?status=translated').galleryStatus, 'translated');
  assert.equal(parseAppPath('/gallery', '?status=original').galleryStatus, 'original');
  assert.equal(parseAppPath('/gallery', '?status=summarized').galleryStatus, 'summarized');
  assert.equal(parseAppPath('/gallery', '?status=review').galleryStatus, 'review');
  assert.equal(parseAppPath('/gallery', '?status=review').reviewOnly, true);
  assert.equal(parseAppPath('/gallery', '?review=all').reviewOnly, false);
  assert.equal(parseAppPath('/gallery', '?view=series').gallerySection, 'series');
  assert.equal(parseAppPath('/gallery/series/series-1').seriesId, 'series-1');

  // Trailing slash normalization
  const trailing = parseAppPath('/gallery/');
  assert.equal(trailing.view, 'gallery');
  assert.equal(trailing.overlay, 'none');

  const trailingManga = parseAppPath('/gallery/manga/Solo%20Leveling/');
  assert.equal(trailingManga.view, 'gallery');
  assert.equal(trailingManga.overlay, 'none');
  assert.equal(trailingManga.mangaTitle, 'Solo Leveling');

  // Root defaults to studio
  const root = parseAppPath('/');
  assert.equal(root.view, 'studio');
  assert.equal(root.overlay, 'none');
}

// 2. getLegacyRedirect tests
{
  assert.equal(getLegacyRedirect('/'), '/studio');
  assert.equal(getLegacyRedirect('/', '?view=studio'), '/studio');
  assert.equal(getLegacyRedirect('/', '?view=gallery'), '/gallery');
  assert.equal(getLegacyRedirect('/', '?view=gallery&manga=Solo%20Leveling'), buildMangaDetailUrl('Solo Leveling'));
  assert.equal(getLegacyRedirect('/gallery'), null);
  assert.equal(getLegacyRedirect('/gallery', '?manga=Solo%20Leveling'), buildMangaDetailUrl('Solo Leveling'));
  assert.equal(getLegacyRedirect('/studio'), null);
  assert.equal(getLegacyRedirect('/pipeline-lab'), null);
}

// 3. validatePriorRoute tests
{
  assert.equal(validatePriorRoute('/studio'), '/studio');
  assert.equal(validatePriorRoute('/gallery'), '/gallery');
  assert.equal(validatePriorRoute('/gallery?search=doujinshi'), '/gallery?search=doujinshi');
  assert.equal(validatePriorRoute('/gallery/manga/Solo%20Leveling'), '/gallery/manga/Solo%20Leveling');
  assert.equal(validatePriorRoute('/gallery/series/series-1'), '/gallery/series/series-1');
  assert.equal(validatePriorRoute('/gallery/pages/xyz'), '/gallery');
  assert.equal(validatePriorRoute('https://malicious.com'), '/gallery');
  assert.equal(validatePriorRoute(null), '/gallery');
  assert.equal(validatePriorRoute(undefined), '/gallery');
  assert.equal(validatePriorRoute('/invalid', '/studio'), '/studio');
}

// 3b. Overlay transitions keep the original base route
{
  const mangaDetail = '/gallery/manga/manga-123';
  assert.equal(getNavigationOrigin(mangaDetail, '', '/gallery/series/series-1'), mangaDetail);
  assert.equal(getNavigationOrigin('/gallery/pages/page-2', '', mangaDetail), mangaDetail);
  assert.equal(getNavigationOrigin('/read', '?manga=manga-123', mangaDetail), mangaDetail);
  assert.equal(getNavigationOrigin('/gallery/pages/page-3', '', '/gallery/pages/page-2'), '/gallery');
}

// 4. URL builders
{
  assert.equal(buildReaderUrl('Manga & Anime'), `/read?manga=${mangaIdForTitle('Manga & Anime')}`);
  assert.equal(buildReaderIdUrl('550e8400-e29b-41d4-a716-446655440000'), '/read?manga=550e8400-e29b-41d4-a716-446655440000');
  assert.equal(buildPageViewUrl('folder 1'), '/gallery/pages/folder%201');
  assert.equal(buildPageEditUrl('folder 1'), '/gallery/pages/folder%201/edit');
  assert.equal(buildMangaDetailUrl('Solo Leveling'), `/gallery/manga/${mangaIdForTitle('Solo Leveling')}`);
  assert.equal(buildMangaDetailUrl('Solo Leveling', true), `/gallery/manga/${mangaIdForTitle('Solo Leveling')}?review=pending`);
  assert.equal(buildMangaDetailUrl('Manga & Anime'), `/gallery/manga/${mangaIdForTitle('Manga & Anime')}`);
  assert.equal(buildSeriesDetailUrl('series-1'), '/gallery/series/series-1');
assert.equal(buildGalleryPageUrl(2, 25, '', 'series'), '/gallery?view=series&page=2');
assert.equal(buildGalleryPageUrl(1, 25, '', 'manga', 'date-desc', true), '/gallery?status=review&review=pending');
assert.equal(buildGalleryPageUrl(1, 25, '', 'manga', 'date-desc', false, 'translated'), '/gallery?status=translated');
assert.equal(buildGalleryPageUrl(2, 25, '', 'manga', 'date-desc', false, 'translated'), '/gallery?page=2&status=translated');
assert.equal(buildGalleryPageUrl(3, 25, '', 'manga', 'date-desc', false, 'original'), '/gallery?page=3&status=original');
assert.equal(buildGalleryPageUrl(2, 25, '', 'manga', 'date-desc', false, 'summarized'), '/gallery?page=2&status=summarized');
assert.equal(buildGalleryPageUrl(2, 50, 'One Piece', 'manga', 'date-desc', false, 'translated'), '/gallery?page=2&pageSize=50&search=One+Piece&status=translated');
assert.equal(buildGalleryPageUrl(1, 25, '', 'manga', 'date-desc', false, 'all'), '/gallery');
  assert.equal(buildGalleryPageUrl(1), '/gallery');
  assert.equal(buildGalleryPageUrl(2), '/gallery?page=2');
  assert.equal(buildGalleryPageUrl(1, 25), '/gallery');
  assert.equal(buildGalleryPageUrl(2, 25), '/gallery?page=2');
  assert.equal(buildGalleryPageUrl(1, 24, 'Solo Leveling'), '/gallery?pageSize=24&search=Solo+Leveling');
  assert.equal(buildMangaDetailUrl(''), '/gallery');
}

console.log('All routeState tests passed successfully!');
