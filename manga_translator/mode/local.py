import fnmatch
import os
import pathlib
import json
import copy
import stat
import tempfile
import zipfile
from typing import Union, List, Tuple
import time  

from PIL import Image
import psutil

from manga_translator import MangaTranslator, Context, TranslationInterrupt, Config
from ..save import save_result
from ..translators import (
    LanguageUnsupportedException,
    dispatch as dispatch_translation,
)
from ..translators.gemini_keys import GeminiRetryExhausted
from ..utils import natural_sort, replace_prefix, get_color_name, rgb2hex, get_logger
from ..utils.device_memory import empty_device_cache

# 使用专用的local logger
logger = get_logger('local')

# 提示音开关
ENABLE_COMPLETION_SOUND = True
ARCHIVE_EXTENSIONS = {'.cbz', '.zip'}


def is_archive_path(path: str) -> bool:
    return pathlib.Path(path).suffix.lower() in ARCHIVE_EXTENSIONS


def archive_output_path(path: str, dest: str = '', single_input: bool = False) -> str:
    suffix = pathlib.Path(path).suffix
    if single_input and dest and pathlib.Path(dest).suffix.lower() in ARCHIVE_EXTENSIONS:
        return os.path.abspath(os.path.expanduser(dest))
    output_dir = os.path.abspath(os.path.expanduser(dest)) if dest else os.path.dirname(os.path.abspath(path))
    return os.path.join(output_dir, f'{pathlib.Path(path).stem}-translated{suffix}')


def _safe_extract_archive(archive: zipfile.ZipFile, destination: str):
    root = pathlib.Path(destination).resolve()
    for member in archive.infolist():
        target = (root / member.filename).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f'Unsafe archive member path: {member.filename}')
        if stat.S_ISLNK(member.external_attr >> 16):
            raise ValueError(f'Symbolic links are not supported in archives: {member.filename}')
    archive.extractall(root)


def _write_translated_archive(source: str, translated: str, dest: str):
    source_root = pathlib.Path(source)
    translated_root = pathlib.Path(translated)
    output_files = {}

    if translated_root.exists():
        output_files.update({
            path.relative_to(translated_root).as_posix(): path
            for path in translated_root.rglob('*')
            if path.is_file()
        })

    image_extensions = set(Image.registered_extensions())
    for path in source_root.rglob('*'):
        relative = path.relative_to(source_root).as_posix()
        if path.is_file() and relative not in output_files and pathlib.Path(relative).suffix.lower() not in image_extensions:
            output_files[relative] = path

    os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(output_files):
            archive.write(output_files[relative], relative)

def play_completion_sound():
    """播放完成提示音"""
    try:
        import platform
        if platform.system() == 'Windows':
            import winsound
            # 使用默认系统提示音
            winsound.MessageBeep(-1)
        else:
            # 其他平台使用控制台蜂鸣声
            print('\a', end='', flush=True)
    except Exception as e:
        # 提示音失败不影响主程序
        logger.debug(f'Failed to play completion sound: {e}')

def safe_get_memory_info():
    """安全获取内存信息，失败时返回默认值"""
    try:
        memory = psutil.virtual_memory()
        return memory.percent, memory.available // (1024 * 1024)  # 可用内存MB
    except Exception as e:
        logger.warning(f'Unable to get memory info: {e}')
        return 95.0, 100  # 假设高内存使用率，低可用内存

def parse_exclude_spec(exclude_spec: str):
    if not exclude_spec:
        return set(), []
    page_numbers = set()
    patterns = []
    for token in exclude_spec.split(','):
        token = token.strip()
        if not token:
            continue
        if '-' in token:
            parts = token.split('-', 1)
            if parts[0].isdigit() and parts[1].isdigit():
                start, end = int(parts[0]), int(parts[1])
                for p in range(min(start, end), max(start, end) + 1):
                    page_numbers.add(p)
                continue
        if token.isdigit():
            page_numbers.add(int(token))
        else:
            patterns.append(token.lower())
    return page_numbers, patterns


def is_page_color_excluded(file_path: str, page_num: int, page_numbers: set, patterns: list) -> bool:
    if page_num in page_numbers:
        return True
    base = os.path.basename(file_path).lower()
    for pat in patterns:
        if fnmatch.fnmatch(base, pat):
            return True
    return False


def force_cleanup():
    """强制内存清理"""
    logger.debug('Performing force memory cleanup...')
    empty_device_cache()

class MangaTranslatorLocal(MangaTranslator):
    def __init__(self, params: dict = None):
        super().__init__(params)
        self.textlines = []
        self.attempts = params.get('attempts', None)
        self.skip_no_text = params.get('skip_no_text', False)
        self.text_output_file = params.get('text_output_file', None)
        self.save_quality = params.get('save_quality', None)
        self.text_regions = params.get('text_regions', None)
        self.save_text_file = params.get('save_text_file', None)
        self.save_text = params.get('save_text', None)
        self.prep_manual = params.get('prep_manual', None)
        self.batch_size = params.get('batch_size', 20)
        self.disable_memory_optimization = params.get('disable_memory_optimization', False)

    def _load_config_for_path(self, params: dict) -> Config:
        config_file_path = params.get("config_file", None)
        if not config_file_path:
            candidates = ["config_macos.json", "config.json"] if sys.platform == 'darwin' else ["config.json"]
            for cand in candidates:
                cand_path = os.path.join(os.getcwd(), cand)
                if os.path.isfile(cand_path):
                    config_file_path = cand_path
                    logger.info(f"Auto-loaded configuration from: {cand}")
                    break

        if not config_file_path:
            return Config()

        try:
            with open(config_file_path, 'r', encoding='utf-8') as file:
                config_content = file.read()
        except Exception as e:
            print("Couldnt read file")
            raise e

        config_extension = os.path.splitext(config_file_path)[1].lower()
        try:
            if config_extension == ".toml":
                import tomllib
                config_dict = tomllib.loads(config_content)
            elif config_extension == ".json":
                config_dict = json.loads(config_content)
            else:
                raise ValueError("Unsupported configuration file format")
        except Exception as e:
            print("Failed to load configuration file")
            raise e

        return Config(**config_dict)

    def _apply_config_cli_overrides(self, config: Config, params: dict):
        if params.get('colorizer'):
            from ..config import Colorizer
            config.colorizer.colorizer = Colorizer(params['colorizer'])
        if params.get('colorization_size') is not None:
            config.colorizer.colorization_size = params['colorization_size']
        if params.get('denoise_sigma') is not None:
            config.colorizer.denoise_sigma = params['denoise_sigma']
        if params.get('color_threshold') is not None:
            config.colorizer.color_threshold = params['color_threshold']
        elif params.get('color_tolerance') is not None:
            config.colorizer.color_threshold = params['color_tolerance']
        if params.get('restore_size') is not None:
            config.colorizer.restore_size = params['restore_size']

        if params.get('uppercase'):
            config.render.uppercase = True
            config.render.lowercase = False
        elif params.get('lowercase'):
            config.render.lowercase = True
            config.render.uppercase = False
        elif params.get('letter_case'):
            val = str(params['letter_case']).strip().lower()
            if val in ('upper', 'uppercase', 'all_caps', 'caps'):
                config.render.uppercase = True
                config.render.lowercase = False
            elif val in ('lower', 'lowercase'):
                config.render.lowercase = True
                config.render.uppercase = False
            elif val in ('none', 'original', 'default'):
                config.render.uppercase = False
                config.render.lowercase = False

        if params.get('colorize_only'):
            from ..config import Colorizer, Detector, Inpainter, Translator, Renderer
            if not params.get('colorizer') or params.get('colorizer') == 'none':
                config.colorizer.colorizer = Colorizer.mc2
            else:
                config.colorizer.colorizer = Colorizer(params['colorizer'])
            config.detector.detector = Detector.none
            config.inpainter.inpainter = Inpainter.original
            config.translator.translator = Translator.none
            config.render.renderer = Renderer.none

    def _get_single_file_dest(self, path: str, dest: str, tag: str, file_ext: str) -> str:
        if not dest:
            p, ext = os.path.splitext(path)
            return f'{p}{tag}.{file_ext or ext[1:]}'
        if not os.path.basename(dest):
            p, ext = os.path.splitext(os.path.basename(path))
            if os.path.dirname(path) != dest:
                return os.path.join(dest, f'{p}.{file_ext or ext[1:]}')
            return os.path.join(dest, f'{p}{tag}.{file_ext or ext[1:]}')
        p, ext = os.path.splitext(dest)
        return f'{p}.{file_ext or ext[1:]}'

    def _log_translation_completion(self, total_time: float, translated_count: int, dest: str):
        if translated_count == 0:
            logger.info('No further untranslated files found. Use --overwrite to write over existing translations.')
            return

        if total_time >= 3600:
            time_str = f"{total_time/3600:.1f} hours"
        elif total_time >= 60:
            time_str = f"{total_time/60:.1f} minutes"
        else:
            time_str = f"{total_time:.1f} seconds"

        logger.info(f'Done. Translated {translated_count} image{"" if translated_count == 1 else "s"} in {time_str}')
        logger.info(f'Results saved to: "{dest}"')
        try:
            if ENABLE_COMPLETION_SOUND:
                play_completion_sound()
        except Exception as e:
            logger.debug(f'Failed to play completion sound: {e}')

    async def _translate_folder_sequential(self, path: str, dest: str, params: dict, config: Config, file_ext: str):
        start_time = time.time()
        translated_count = 0
        page_idx = 0
        exclude_color_pages = params.get('exclude_color_pages')
        page_numbers, patterns = parse_exclude_spec(exclude_color_pages)

        for root, subdirs, files in os.walk(path):
            files = natural_sort(files)
            dest_root = replace_prefix(root, path, dest)
            os.makedirs(dest_root, exist_ok=True)

            for f in files:
                if f.lower() == '.thumb':
                    continue

                page_idx += 1
                file_path = os.path.join(root, f)
                output_dest = replace_prefix(file_path, path, dest)
                p, ext = os.path.splitext(output_dest)
                output_dest = f'{p}.{file_ext or ext[1:]}'

                page_config = config
                if is_page_color_excluded(file_path, page_idx, page_numbers, patterns):
                    from ..config import Colorizer
                    page_config = copy.deepcopy(config)
                    page_config.colorizer.colorizer = Colorizer.none
                    logger.info(f'Page {page_idx} ({f}) excluded from colorization via --exclude-color-pages')

                try:
                    if await self.translate_file(file_path, output_dest, params, page_config):
                        translated_count += 1
                except Exception as e:
                    logger.error(e)
                    raise e
                finally:
                    force_cleanup()

        self._log_translation_completion(time.time() - start_time, translated_count, dest)

    async def translate_path(self, path: str, dest: str = None, params: dict[str, Union[int, str]] = None):
        """
        Translates an image or folder (recursively) specified through the path.
        """
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        path = os.path.abspath(os.path.expanduser(path))
        dest = os.path.abspath(os.path.expanduser(dest)) if dest else ''
        params = params or {}

        config = self._load_config_for_path(params)
        self._apply_config_cli_overrides(config, params)

        file_ext = params.get('format')
        if params.get('save_quality', 100) < 100:
            if not file_ext:
                file_ext = 'jpg'
            elif file_ext != 'jpg':
                raise ValueError('--save-quality of lower than 100 is only supported for .jpg files')

        tag = '-colorized' if params.get('colorize_only') else '-translated'

        if os.path.isfile(path):
            _dest = self._get_single_file_dest(path, dest, tag, file_ext)
            await self.translate_file(path, _dest, params, config)
            return

        if os.path.isdir(path):
            if path[-1] in ('\\', '/'):
                path = path[:-1]
            _dest = dest or path + tag
            if os.path.exists(_dest) and not os.path.isdir(_dest):
                raise FileExistsError(_dest)

            if self.batch_size > 1:
                await self._translate_folder_batch(path, _dest, params, config, file_ext)
            else:
                await self._translate_folder_sequential(path, _dest, params, config, file_ext)

    async def translate_archive(self, path: str, dest: str, params: dict[str, Union[int, str]] = None):
        params = params or {}
        if not params.get('overwrite') and os.path.exists(dest):
            logger.info(f'Skipping as already translated: "{dest}". Use --overwrite to overwrite the archive.')
            return

        with tempfile.TemporaryDirectory(prefix='manga-translator-') as workspace:
            source = os.path.join(workspace, 'source')
            translated = os.path.join(workspace, 'translated')
            os.makedirs(source)
            with zipfile.ZipFile(path) as archive:
                _safe_extract_archive(archive, source)
            await self.translate_path(source, translated, params)
            _write_translated_archive(source, translated, dest)
            logger.info(f'Saved translated archive: "{dest}"')

    async def translate_file(self, path: str, dest: str, params: dict, config: Config):
        if not params.get('overwrite') and os.path.exists(dest):
            logger.info(
                f'Skipping as already translated: "{dest}". Use --overwrite to overwrite existing translations.')
            await self._report_progress('saved', True)
            return True

        logger.info(f'Translating: "{path}"')

        # Turn dict to context to make values also accessible through params.<property>
        params = params or {}
        ctx = Context(**params)

        attempts = 0
        while self.attempts == -1 or attempts < self.attempts + 1:
            if attempts > 0:
                logger.info(f'Retrying translation! Attempt {attempts}'
                            + (f' of {self.attempts}' if self.attempts != -1 else ''))
            try:
                return await self._translate_file(path, dest, config, ctx)

            except TranslationInterrupt:
                break
            except GeminiRetryExhausted as e:
                await self._report_progress('error', True)
                logger.error(f'{e.__class__.__name__}: {e}')
                if not self.ignore_errors:
                    raise
                return False
            except Exception as e:
                if isinstance(e, LanguageUnsupportedException):
                    await self._report_progress('error-lang', True)
                else:
                    await self._report_progress('error', True)
                if not self.ignore_errors and not (self.attempts == -1 or attempts < self.attempts):
                    raise
                else:
                    logger.error(f'{e.__class__.__name__}: {e}',
                                 exc_info=e if self.verbose else None)
            attempts += 1
        return False

    async def _translate_file(self, path: str, dest: str, config: Config, ctx: Context) -> bool:
        if path.endswith('.txt'):
            with open(path, 'r') as f:
                queries = f.read().split('\n')
            translated_sentences = \
                await dispatch_translation(config.translator.translator_gen, queries, self.use_mtpe, ctx,
                                           'cpu' if self._gpu_limited_memory else self.device)
            p, ext = os.path.splitext(dest)
            if ext != '.txt':
                dest = p + '.txt'
            logger.info(f'Saving "{dest}"')
            with open(dest, 'w') as f:
                f.write('\n'.join(translated_sentences))
            return True

        # TODO: Add .gif handler

        else:  # Treat as image
            try:
                img = Image.open(path)
                img.verify()
                img = Image.open(path)
            except Exception:
                logger.warn(f'Failed to open image: {path}')
                return False

            # 直接翻译图片，不再需要传递文件名
            ctx = await self.translate(img, config)
            result = ctx.result

            # TODO
            # Proper way to use the config but for now juste pass what we miss here ton ctx
            # Because old methods are still using for example ctx.gimp_font
            # Not done before because we change the ctx few lines above
            ctx.gimp_font = config.render.gimp_font

            # Save result
            if self.skip_no_text and not ctx.text_regions:
                logger.debug('Not saving due to --skip-no-text')
                return True
            if result:
                logger.info(f'Saving "{dest}"')
                ctx.save_quality = self.save_quality
                save_result(result, dest, ctx)
                await self._report_progress('saved', True)

                if self.save_text or self.save_text_file or self.prep_manual:
                    if self.prep_manual:
                        # Save original image next to translated
                        p, ext = os.path.splitext(dest)
                        img_filename = p + '-orig' + ext
                        img_path = os.path.join(os.path.dirname(dest), img_filename)
                        img.save(img_path, quality=self.save_quality)
                    if self.text_regions:
                        self._save_text_to_file(path, ctx)
                return True
        return False

    def _save_text_to_file(self, image_path: str, ctx: Context):
        cached_colors = []

        def identify_colors(fg_rgb: List[int]):
            idx = 0
            for rgb, _ in cached_colors:
                # If similar color already saved
                if abs(rgb[0] - fg_rgb[0]) + abs(rgb[1] - fg_rgb[1]) + abs(rgb[2] - fg_rgb[2]) < 50:
                    break
                else:
                    idx += 1
            else:
                cached_colors.append((fg_rgb, get_color_name(fg_rgb)))
            return idx + 1, cached_colors[idx][1]

        s = f'\n[{image_path}]\n'
        for i, region in enumerate(ctx.text_regions):
            fore, back = region.get_font_colors()
            color_id, color_name = identify_colors(fore)

            s += f'\n-- {i + 1} --\n'
            s += f'color: #{color_id}: {color_name} (fg, bg: {rgb2hex(*fore)} {rgb2hex(*back)})\n'
            s += f'text:  {region.text}\n'
            s += f'trans: {region.translation}\n'
            for line in region.lines:
                s += f'coords: {list(line.ravel())}\n'
        s += '\n'

        text_output_file = self.text_output_file
        if not text_output_file:
            text_output_file = os.path.splitext(image_path)[0] + '_translations.txt'

        with open(text_output_file, 'a', encoding='utf-8') as f:
            f.write(s)

    def _collect_folder_batch_tasks(self, path: str, dest: str, params: dict, config: Config, file_ext: str):
        image_tasks = []
        page_idx = 0
        exclude_color_pages = params.get('exclude_color_pages')
        page_numbers, patterns = parse_exclude_spec(exclude_color_pages)

        for root, subdirs, files in os.walk(path):
            files = natural_sort(files)
            dest_root = replace_prefix(root, path, dest)
            os.makedirs(dest_root, exist_ok=True)

            for f in files:
                if f.lower() == '.thumb':
                    continue

                page_idx += 1
                file_path = os.path.join(root, f)
                output_dest = replace_prefix(file_path, path, dest)
                p, ext = os.path.splitext(output_dest)
                output_dest = f'{p}.{file_ext or ext[1:]}'

                if not params.get('overwrite') and os.path.exists(output_dest):
                    logger.debug(f'Skipping already translated file: "{output_dest}"')
                    continue

                page_config = config
                if is_page_color_excluded(file_path, page_idx, page_numbers, patterns):
                    from ..config import Colorizer
                    page_config = copy.deepcopy(config)
                    page_config.colorizer.colorizer = Colorizer.none
                    logger.info(f'Page {page_idx} ({f}) excluded from colorization via --exclude-color-pages')

                try:
                    # Validate without retaining the decoded image; actual pixels are
                    # loaded only for the current batch.
                    with Image.open(file_path) as opened:
                        opened.verify()
                    image_tasks.append((page_config, file_path, output_dest))
                except Exception as e:
                    logger.warning(f'Failed to open image: {file_path}, error: {e}')

        return image_tasks

    def _determine_batch_item_save_reason(self, ctx, file_path: str) -> Tuple[bool, str]:
        has_original_text = ctx and hasattr(ctx, 'text_regions') and ctx.text_regions
        if not ctx:
            logger.warning(f'Translation failed: {file_path} (context is None)')
            return True, "no_context"
        if not hasattr(ctx, 'result'):
            logger.warning(f'Translation failed: {file_path} (no result attribute)')
            return True, "no_result_attr"
        if ctx.result is None:
            if not has_original_text:
                return True, "no_original_text"

            filtered_by_processing = all(
                hasattr(region, 'translation') and 
                (not region.translation.strip() or 
                 region.translation.isnumeric() or 
                 region.text.lower().strip() == region.translation.lower().strip())
                for region in ctx.text_regions
            ) if ctx.text_regions else False

            if filtered_by_processing:
                return True, "filtered_translation"
            return False, "translation_failed_with_text"

        logger.warning(f'Translation failed: {file_path} (unexpected condition)')
        return True, "unexpected"

    def _save_batch_original_image(self, img, output_dest: str, file_path: str, save_reason: str) -> bool:
        logger.info(f'Saving original image ({save_reason}): {file_path}')
        try:
            os.makedirs(os.path.dirname(output_dest), exist_ok=True)
            if self.save_quality and self.save_quality < 100:
                img_copy = img.convert('RGB') if img.mode != 'RGB' else img.copy()
                img_copy.save(output_dest, quality=self.save_quality, format='JPEG')
            else:
                img.save(output_dest)
            logger.info(f'Original image saved: "{output_dest}"')
            return True
        except Exception as save_error:
            logger.error(f'Failed to save original image: {file_path}, error: {save_error}')
            return False

    def _save_batch_item(self, ctx, img, file_path: str, output_dest: str, batch_config: Config, params: dict) -> bool:
        if self.skip_no_text and ctx and not ctx.text_regions:
            logger.debug(f'Not saving due to --skip-no-text: {file_path}')
            return False

        if ctx and ctx.result:
            logger.debug(f'Saving translation result: "{output_dest}"')
            save_ctx = Context(**params)
            save_ctx.result = ctx.result
            save_ctx.text_regions = ctx.text_regions
            save_ctx.gimp_font = batch_config.render.gimp_font
            save_ctx.save_quality = self.save_quality
            save_result(ctx.result, output_dest, save_ctx)

            if self.save_text or self.save_text_file or self.prep_manual:
                if self.prep_manual:
                    p, ext = os.path.splitext(output_dest)
                    img_filename = p + '-orig' + ext
                    img_path = os.path.join(os.path.dirname(output_dest), img_filename)
                    img.save(img_path, quality=self.save_quality)
                if ctx.text_regions:
                    self._save_text_to_file(file_path, ctx)
            return True

        if ctx and ctx.get('translation_error'):
            logger.error(f'Page failed; left unsaved for retry: {file_path}: {ctx.translation_error}')
            return False

        should_save, save_reason = self._determine_batch_item_save_reason(ctx, file_path)
        if not should_save:
            logger.debug(f'Skipped saving for retry: {file_path}')
            return False

        if self.skip_no_text:
            logger.debug(f'Skipped saving due to --skip-no-text: {file_path}')
            return False

        return self._save_batch_original_image(img, output_dest, file_path, save_reason)

    async def _process_batch_chunk(
        self,
        batch,
        batch_num: int,
        config: Config,
        memory_optimization_enabled: bool,
        params: dict,
    ) -> int:
        memory_percent, available_mb = safe_get_memory_info()
        if memory_optimization_enabled and memory_percent > 90:
            logger.warning(f'High memory usage detected ({memory_percent:.1f}%), forcing cleanup...')
            force_cleanup()
            memory_percent, available_mb = safe_get_memory_info()
            logger.info(f'Memory status after cleanup: {memory_percent:.1f}%, available: {available_mb}MB')

        batch_config = copy.deepcopy(config) if memory_optimization_enabled else config
        images = []
        try:
            for _, file_path, _ in batch:
                with Image.open(file_path) as opened:
                    images.append(opened.copy())
        except Exception as e:
            for img in images:
                img.close()
            logger.warning(f'Failed to open batch image: {e}')
            if not self.ignore_errors:
                raise
            return 0

        images_with_configs = [(img, batch_config) for img in images]

        translated_count = 0
        try:
            logger.debug(f'Starting batch translation for {len(batch)} images...')
            batch_results = await self.translate_batch(images_with_configs, len(batch))

            for ctx, (img, (_, file_path, output_dest)) in zip(batch_results, zip(images, batch)):
                if self._save_batch_item(ctx, img, file_path, output_dest, batch_config, params):
                    translated_count += 1
                if hasattr(ctx, 'cleanup_all_images'):
                    ctx.cleanup_all_images()

            logger.debug(f'Batch {batch_num} processed successfully')
        except (MemoryError, OSError) as e:
            logger.error(f'Memory error in batch processing: {e}')
            if not memory_optimization_enabled:
                logger.error('Consider enabling memory optimization (remove --disable-memory-optimization flag)')
                raise
        except Exception as e:
            logger.error(f'Other error in batch processing: {e}')
            if not self.ignore_errors:
                raise
        finally:
            for img in images:
                try:
                    img.close()
                except Exception:
                    pass
            images.clear()
            force_cleanup()

        return translated_count

    async def _translate_folder_batch(self, path: str, dest: str, params: dict, config: Config, file_ext: str):
        """使用批量处理方式翻译文件夹中的图片"""
        start_time = time.time()
        memory_percent, available_mb = safe_get_memory_info()
        logger.info(f'Batch processing started - batch size: {self.batch_size}, memory usage: {memory_percent:.1f}%, available: {available_mb}MB')

        memory_optimization_enabled = not self.disable_memory_optimization
        logger.info('Memory optimization ' + ('disabled by user' if not memory_optimization_enabled else 'enabled'))

        image_tasks = self._collect_folder_batch_tasks(path, dest, params, config, file_ext)
        if not image_tasks:
            logger.info('No images found to translate, use --overwrite to write over existing translations.')
            return

        logger.info(f'Found {len(image_tasks)} images to translate')
        base_batch_size = self.batch_size
        translated_count = 0
        i = 0

        while i < len(image_tasks):
            batch = image_tasks[i:i + base_batch_size]
            batch_num = i // base_batch_size + 1
            total_batches = (len(image_tasks) + base_batch_size - 1) // base_batch_size

            logger.info(f'Processing batch {batch_num}/{total_batches} (size: {len(batch)})')
            memory_percent, available_mb = safe_get_memory_info()
            logger.debug(f'Memory status before batch: {memory_percent:.1f}%, available: {available_mb}MB')

            translated_count += await self._process_batch_chunk(
                batch, batch_num, config, memory_optimization_enabled, params
            )

            del batch

            force_cleanup()
            memory_percent, available_mb = safe_get_memory_info()
            logger.debug(f'Memory status after batch {batch_num}: {memory_percent:.1f}%, available: {available_mb}MB')
            i += base_batch_size

        self._log_translation_completion(time.time() - start_time, translated_count, dest)
