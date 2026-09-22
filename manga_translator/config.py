import argparse
import re
import sys
from enum import Enum

from typing import Optional, Any, Literal, List

from omegaconf import OmegaConf
from pydantic import BaseModel, Field, field_validator, model_validator

MAX_MANGA_TITLE_LENGTH = 200


# TODO: Refactor
class TranslatorChain:
    def __init__(self, string: str):
        """
        Parses string in form 'trans1:lang1;trans2:lang2' into chains,
        which will be executed one after another when passed to the dispatch function.
        """
        from manga_translator.translators import TRANSLATORS, VALID_LANGUAGES
        if not string:
            raise Exception('Invalid translator chain')
        self.chain = []
        self.target_lang = None
        for g in string.split(';'):
            trans, lang = g.split(':')
            try:
                translator = Translator(trans)
            except Exception:
                raise ValueError(f'Invalid choice: %s (choose from %s)' % (trans, ', '.join(map(repr, TRANSLATORS))))
            if lang not in VALID_LANGUAGES:
                raise ValueError(f'Invalid choice: %s (choose from %s)' % (lang, ', '.join(map(repr, VALID_LANGUAGES))))
            self.chain.append((translator, lang))
        self.translators, self.langs = list(zip(*self.chain))

    def has_offline(self) -> bool:
        """
        Returns True if the chain contains offline translators.
        """
        from manga_translator.translators import OFFLINE_TRANSLATORS
        return any(translator in OFFLINE_TRANSLATORS for translator in self.translators)

    def __eq__(self, __o: object) -> bool:
        if type(__o) is str:
            return __o == self.translators[0]
        return super.__eq__(self, __o)


def translator_chain(string):
    try:
        return TranslatorChain(string)
    except ValueError as e:
        raise argparse.ArgumentTypeError(e)
    except Exception:
        raise argparse.ArgumentTypeError(f'Invalid translator_chain value: "{string}". Example usage: --translator "google:sugoi" -l "JPN:ENG"')


def hex2rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

class Renderer(str, Enum):
    default = "default"
    manga2Eng = "manga2eng"
    manga2EngPillow = "manga2eng_pillow"
    none = "none"

    def __str__(self):
        return self.value

    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, str):
            raise ValueError(f"{value} is not a valid {cls.__name__}")
        val = value.lower().replace('-', '_')
        if val in ('default',):
            return cls.default
        if val in ('manga2eng', 'manga2_eng'):
            return cls.manga2Eng
        if val in ('manga2eng_pillow', 'manga2_eng_pillow', 'pillow'):
            return cls.manga2EngPillow
        if val in ('none', 'null', 'off', 'disabled'):
            return cls.none
        raise ValueError(f"{value} is not a valid {cls.__name__}")

class Alignment(str, Enum):
    auto = "auto"
    left = "left"
    center = "center"
    right = "right"

class Direction(str, Enum):
    auto = "auto"
    h = "horizontal"
    v = "vertical"

class InpaintPrecision(str, Enum):
    fp32 = "fp32"
    fp16 = "fp16"
    bf16 = "bf16"

    def __str__(self):
        return self.name

class Detector(str, Enum):
    default = "default"
    dbconvnext = "dbconvnext"
    ctd = "ctd"
    craft = "craft"
    paddle = "paddle"
    none = "none"

    def __str__(self):
        return self.value

    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, str):
            raise ValueError(f"{value} is not a valid {cls.__name__}")
        val = value.lower().replace('-', '_')
        if val in ('manga_text_detector', 'manga_text_detector_default', 'dbnet_resnet34', 'default'):
            return cls.default
        if val in ('dbconvnext', 'dbnet_convnext', 'dbnet', 'db_convnext'):
            return cls.dbconvnext
        if val in ('ctd', 'comic_text_detector'):
            return cls.ctd
        if val in ('craft',):
            return cls.craft
        if val in ('paddle', 'paddle_rust', 'paddlerust'):
            return cls.paddle
        if val in ('none', 'null', 'off', 'disabled', 'no', 'false'):
            return cls.none
        raise ValueError(f"{value} is not a valid {cls.__name__}")

class Inpainter(str, Enum):
    default = "default"
    lama_large = "lama_large"
    lama_mpe = "lama_mpe"
    sd = "sd"
    none = "none"
    original = "original"

    def __str__(self):
        return self.value

    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, str):
            raise ValueError(f"{value} is not a valid {cls.__name__}")
        val = value.lower().replace('-', '_')
        if val in ('default', 'aot'):
            return cls.default
        if val in ('lama_large', 'lama', 'big_lama'):
            return cls.lama_large
        if val in ('lama_mpe',):
            return cls.lama_mpe
        if val in ('sd', 'stable_diffusion'):
            return cls.sd
        if val in ('none', 'null', 'off', 'disabled'):
            return cls.none
        if val in ('original', 'orig'):
            return cls.original
        raise ValueError(f"{value} is not a valid {cls.__name__}")

class Colorizer(str, Enum):
    none = "none"
    mc2 = "mc2"

    def __str__(self):
        return self.value

    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, str):
            raise ValueError(f"{value} is not a valid {cls.__name__}")
        val = value.lower().replace('-', '_')
        if val in ('none', 'null', 'off', 'disabled'):
            return cls.none
        if val in ('mc2', 'manga_colorization_v2', 'mangacolorizationv2'):
            return cls.mc2
        raise ValueError(f"{value} is not a valid {cls.__name__}")

class Ocr(str, Enum):
    ocr32px = "32px"
    ocr48px = "48px"
    ocr48px_ctc = "48px_ctc"
    mocr = "mocr"

    def __str__(self):
        return self.value

    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, str):
            raise ValueError(f"{value} is not a valid {cls.__name__}")
        val = value.lower().replace('-', '_')
        if val in ('32px', 'ocr32px', 'ocr_32px'):
            return cls.ocr32px
        if val in ('48px', 'ocr48px', 'ocr_48px'):
            return cls.ocr48px
        if val in ('48px_ctc', 'ocr48px_ctc', 'ocr_48px_ctc', 'ctc'):
            return cls.ocr48px_ctc
        if val in ('mocr', 'manga_ocr', 'mangaocr'):
            return cls.mocr
        raise ValueError(f"{value} is not a valid {cls.__name__}")

class Translator(str, Enum):
    deepseek = "deepseek"
    gemini = "gemini"
    chatgpt = "chatgpt"
    groq = "groq"
    openrouter = "openrouter"
    sugoi = "sugoi"
    custom_openai = "custom_openai"
    sakura = "sakura"
    deepl = "deepl"
    youdao = "youdao"
    baidu = "baidu"
    caiyun = "caiyun"
    none = "none"
    original = "original"

    def __str__(self):
        return self.name

    # Map 'openai', legacy/removed keys, and any translator starting with 'gpt'*
    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, str):
            raise ValueError(f"{value} is not a valid {cls.__name__}")
        val = value.lower()
        if val.startswith('gpt') or val == 'openai':
            return cls.chatgpt
        if val in ('gemini_2stage',):
            return cls.gemini
        if val in ('chatgpt_2stage',):
            return cls.chatgpt
        if val in ('offline', 'jparacrawl', 'jparacrawl_big'):
            return cls.sugoi
        raise ValueError(f"{value} is not a valid {cls.__name__}")


class Upscaler(str, Enum):
    waifu2x = "waifu2x"
    esrgan = "esrgan"
    upscler4xultrasharp = "4xultrasharp"

    def __str__(self):
        return self.value

    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, str):
            raise ValueError(f"{value} is not a valid {cls.__name__}")
        val = value.lower().replace('-', '_')
        if val in ('waifu2x',):
            return cls.waifu2x
        if val in ('esrgan', 'realesrgan', 'real_esrgan'):
            return cls.esrgan
        if val in ('4xultrasharp', '4x_ultrasharp', 'upscler4xultrasharp', 'ultrasharp'):
            return cls.upscler4xultrasharp
        raise ValueError(f"{value} is not a valid {cls.__name__}")

class RenderConfig(BaseModel):
    renderer: Renderer = Renderer.default
    """Render english text translated from manga with some additional typesetting. Ignores some other argument options"""
    alignment: Alignment = Alignment.auto
    """Align rendered text"""
    disable_font_border: bool = False
    """Disable font border"""
    font_size_offset: int = 0
    """Offset font size by a given amount, positive number increase font size and vice versa"""
    font_size_minimum: int = -1
    """Minimum output font size. Default is image_sides_sum/200"""
    direction: Direction = Direction.auto
    """Force text to be rendered horizontally/vertically/none"""
    uppercase: bool = False
    """Change text to uppercase"""
    lowercase: bool = False
    """Change text to lowercase"""
    gimp_font: str = 'Sans-serif'
    """Font family to use for gimp rendering."""
    no_hyphenation: bool = False
    """If renderer should be splitting up words using a hyphen character (-)"""
    font_color: Optional[str] = None
    """Overwrite the text fg/bg color detected by the OCR model. Use hex string without the "#" such as FFFFFF for a white foreground or FFFFFF:000000 to also have a black background around the text."""
    line_spacing: Optional[int] = None
    """Line spacing is font_size * this value. Default is 0.01 for horizontal text and 0.2 for vertical."""
    font_size: Optional[int] = None
    """Use fixed font size for rendering"""
    rtl: bool = True
    """Right-to-left reading order for panel and text_region sorting,"""  
    _font_color_fg = None
    _font_color_bg = None

    @model_validator(mode='before')
    @classmethod
    def _normalize_case_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            letter_case = data.get('letter_case') or data.get('letterCase') or data.get('text_case') or data.get('textCase')
            if letter_case is not None:
                val = str(letter_case).strip().lower()
                if val in ('upper', 'uppercase', 'all_caps', 'caps'):
                    data['uppercase'] = True
                    data['lowercase'] = False
                elif val in ('lower', 'lowercase'):
                    data['lowercase'] = True
                    data['uppercase'] = False
                elif val in ('none', 'original', 'default', 'mixed'):
                    data['uppercase'] = False
                    data['lowercase'] = False
        return data

    def __init__(self, **data):
        letter_case = data.get('letter_case') or data.get('letterCase') or data.get('text_case') or data.get('textCase')
        if letter_case is not None:
            val = str(letter_case).strip().lower()
            if val in ('upper', 'uppercase', 'all_caps', 'caps'):
                data['uppercase'] = True
                data['lowercase'] = False
            elif val in ('lower', 'lowercase'):
                data['lowercase'] = True
                data['uppercase'] = False
            elif val in ('none', 'original', 'default', 'mixed'):
                data['uppercase'] = False
                data['lowercase'] = False
        super().__init__(**data)

    def transform_text_case(self, text: Optional[str]) -> Optional[str]:
        if not text or not isinstance(text, str):
            return text
        if self.uppercase:
            return text.upper()
        if self.lowercase:
            return text.lower()
        return text
    @property
    def font_color_fg(self):
        if self.font_color and not self._font_color_fg:
            colors = self.font_color.split(':')
            try:
                self._font_color_fg = hex2rgb(colors[0]) if colors[0] else None
                self._font_color_bg = hex2rgb(colors[1]) if len(colors) > 1 and colors[1] else None
            except:
                raise Exception(
                    f'Invalid --font-color value: {self.font_color}. Use a hex value such as FF0000')
        return self._font_color_fg

    @property
    def font_color_bg(self):
        if self.font_color and not self._font_color_bg:
            colors = self.font_color.split(':')
            try:              
                self._font_color_fg = hex2rgb(colors[0]) if colors[0] else None
                self._font_color_bg = hex2rgb(colors[1]) if len(colors) > 1 and colors[1] else None
            except:
                raise Exception(
                    f'Invalid --font-color value: {self.font_color}. Use a hex value such as FF0000')
        return self._font_color_bg

class UpscaleConfig(BaseModel):
    upscaler: Upscaler = Upscaler.upscler4xultrasharp if sys.platform == 'darwin' else Upscaler.esrgan
    """Upscaler to use. --upscale-ratio has to be set for it to take effect"""
    revert_upscaling: bool = True
    """Downscales the previously upscaled image after translation back to original size (Use with --upscale-ratio)."""
    upscale_ratio: Optional[int] = None
    """Image upscale ratio applied before detection. Can improve text detection."""

class TranslatorConfig(BaseModel):
    translator: Translator = Translator.deepseek
    """Language translator to use"""
    target_lang: str = 'ENG' #todo: validate VALID_LANGUAGES #todo: convert to enum
    """Destination language"""
    translation_quality: str = 'fast'
    """Translation workflow: fast or professional."""
    translation_batch_size: int = 20
    """Maximum number of pages per Professional draft/editor request."""
    story_page_ranges: Optional[str] = None
    """Optional one-based story ranges, for example 1-12,13-24."""
    story_plan: Optional[dict[str, Any]] = None
    """Structured story boundaries submitted by the web studio."""
    no_text_lang_skip: bool = False
    """Dont skip text that is seemingly already in the target language."""
    skip_lang: Optional[str] = None
    """Skip translation if source image is one of the provide languages, use comma to separate multiple languages. Example: JPN,ENG"""
    gpt_config: Optional[str] = None  # todo: no more path
    """Path to GPT config file, more info in README"""
    translator_chain: Optional[str] = None
    """Output of one translator goes in another. Example: --translator-chain "google:JPN;sugoi:ENG"."""
    selective_translation: Optional[str] = None
    """Select a translator based on detected language in image. Note the first translation service acts as default if the language isn\'t defined. Example: --translator-chain "google:JPN;sugoi:ENG".'"""
    
    # 译后检查配置项
    enable_post_translation_check: bool = True
    """Enable post-translation validation check"""
    keep_failed_pages_for_editing: bool = False
    """Keep pages with failed regions for manual editing instead of failing them."""
    post_check_max_retry_attempts: int = 3
    """Maximum retry attempts for failed translation validation"""
    post_check_repetition_threshold: int = 20
    """Minimum number of consecutive repetitions to trigger hallucination detection"""
    post_check_target_lang_threshold: float = 0.5  
    """Minimum ratio of target language in translation text for ratio check"""
    
    _translator_gen = None
    _gpt_config = None

    @field_validator("translation_quality")
    @classmethod
    def _validate_translation_quality(cls, value: str) -> str:
        if value not in {"fast", "professional"}:
            raise ValueError("translation_quality must be fast or professional")
        return value

    @field_validator("translation_batch_size")
    @classmethod
    def _validate_translation_batch_size(cls, value: int) -> int:
        if not 1 <= value <= 100:
            raise ValueError("translation_batch_size must be between 1 and 100")
        return value

    @property
    def translator_gen(self):
        if self._translator_gen is None:
            if self.selective_translation is not None:
                #todo: refactor TranslatorChain
                trans =  translator_chain(self.selective_translation)
                trans.target_lang = self.target_lang
                self._translator_gen = trans
            elif self.translator_chain is not None:
                trans = translator_chain(self.translator_chain)
                trans.target_lang = trans.langs[0]
                self._translator_gen = trans
            else:
                self._translator_gen = TranslatorChain(f'{str(self.translator)}:{self.target_lang}')
        return self._translator_gen

    @property
    def chatgpt_config(self):
        if self.gpt_config is not None and self._gpt_config is None:
            #todo: load from already loaded file
            self._gpt_config = OmegaConf.load(self.gpt_config)
        return self._gpt_config


class DetectorConfig(BaseModel):
    """"""
    detector: Detector =Detector.default
    """"Text detector used for creating a text mask from an image, DO NOT use craft for manga, it\'s not designed for it"""
    detection_size: int = 2560
    """Size of image used for detection"""
    text_threshold: float = 0.5
    """Threshold for text detection"""
    det_rotate: bool = False
    """Rotate the image for detection. Might improve detection."""
    det_auto_rotate: bool = False
    """Rotate the image for detection to prefer vertical textlines. Might improve detection."""
    det_invert: bool = False
    """Invert the image colors for detection. Might improve detection."""
    det_gamma_correct: bool = False
    """Applies gamma correction for detection. Might improve detection."""
    box_threshold: float = 0.45
    """Threshold for bbox generation"""
    unclip_ratio: float = 2.3
    """How much to extend text skeleton to form bounding box"""

class InpainterConfig(BaseModel):
    inpainter: Inpainter = Inpainter.lama_large
    """Inpainting model to use"""
    inpainting_size: int = 2048
    """Size of image used for inpainting (too large will result in OOM)"""
    inpainting_precision: InpaintPrecision = InpaintPrecision.bf16
    """Inpainting precision for lama, use bf16 while you can."""

class ColorizerConfig(BaseModel):
    colorization_size: int = 576
    """Size of image used for colorization. Set to -1 to use full image size"""
    denoise_sigma: int = 25
    """Used by colorizer and affects color strength, range from 0 to 255 (default 25). -1 turns it off."""
    colorizer: Colorizer = Colorizer.none
    """Colorization model to use."""
    color_threshold: float = 31.0
    """Threshold for detecting if image is already colored (default 31.0). Set to -1 to disable detection."""
    color_tolerance: Optional[float] = None
    """Alias for color_threshold / color tolerance."""
    restore_size: bool = True
    """Whether to restore the colorized image to the original image dimensions with sharp lineart (default True)."""

    def __init__(self, **data):
        if 'color_tolerance' in data and data['color_tolerance'] is not None:
            if 'color_threshold' not in data or data['color_threshold'] is None:
                data['color_threshold'] = data['color_tolerance']
        super().__init__(**data)

class OcrConfig(BaseModel):
    use_mocr_merge: bool = False
    """Use bbox merge when Manga OCR inference."""
    ocr: Ocr = Ocr.ocr48px
    """Optical character recognition (OCR) model to use"""
    min_text_length: int = 0
    """Minimum text length of a text region"""
    ignore_bubble: int = 0
    """The threshold for ignoring text in non bubble areas, with valid values ranging from 1 to 50, does not ignore others. Recommendation 5 to 10. If it is too low, normal bubble areas may be ignored, and if it is too large, non bubble areas may be considered normal bubbles"""
    prob: float | None = None
    """Minimum probability of a text region to be considered valid. If None, uses the model default."""

class BubbleDetectionConfig(BaseModel):
    enabled: bool = False
    """Use the optional Manga109 speech-bubble segmenter."""
    model: str = "manga109"
    confidence: float = 0.25
    mask_threshold: float = 0.5
    image_size: int = 512
    device: str = "auto"
    padding: int = 9
    """Padding erosion in pixels from bubble contour to protect the boundary edge stroke during inpainting."""
    group_regions: bool = False
    """Whether to group and merge text regions falling inside the same detected bubble instance."""

class PipelineLabConfig(BaseModel):
    enabled: bool = False
    stage_plan: dict[str, bool] = Field(default_factory=dict)
    manual: bool = False

class Config(BaseModel):
    # General
    filter_text: Optional[str] = None
    """Filter regions by their text with a regex. Example usage: '.*badtext.*'"""
    render: RenderConfig = RenderConfig()
    """render configs"""
    upscale: UpscaleConfig = UpscaleConfig()
    """upscaler configs"""
    translator: TranslatorConfig = TranslatorConfig()
    """tanslator configs"""
    detector: DetectorConfig = DetectorConfig()
    """detector configs"""
    colorizer: ColorizerConfig = ColorizerConfig()
    """colorizer configs"""
    inpainter: InpainterConfig = InpainterConfig()
    """inpainter configs"""
    ocr: OcrConfig = OcrConfig()
    """Ocr configs"""
    bubble_detection: BubbleDetectionConfig = BubbleDetectionConfig()
    """Optional speech-bubble detection and shape-aware typesetting."""
    pipeline_lab: Optional[PipelineLabConfig] = None
    # ?
    force_simple_sort: bool = False
    """Don't use panel detection for sorting, use a simpler fallback logic instead"""
    kernel_size: int = 3
    """Set the convolution kernel size of the text erasure area to completely clean up text residues"""
    mask_dilation_offset: int = 20
    """By how much to extend the text mask to remove left-over text pixels of the original image."""
    original_name: Optional[str] = None
    """Original file name for tracking and server-side persistence"""
    manga_title: Optional[str] = Field(None, max_length=MAX_MANGA_TITLE_LENGTH)
    """Manga or series title for grouping translated pages"""
    manga_group_id: Optional[str] = None
    """Manga group ID for grouping translated pages in existing group"""
    request_id: Optional[str] = None
    """Stable client request ID used to make retries idempotent"""
    page_order: Optional[int] = None
    """Persistent reading position within the manga group"""
    source_path: Optional[str] = None
    """Original relative path used to disambiguate duplicate filenames"""
    _filter_text = None

    @field_validator("manga_title", mode="before")
    @classmethod
    def _strip_manga_title(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v

    @field_validator("manga_group_id", mode="before")
    @classmethod
    def _strip_manga_group_id(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v

    @property
    def re_filter_text(self):
        if self._filter_text is None:
            self._filter_text = re.compile(self.filter_text)
        return self._filter_text
