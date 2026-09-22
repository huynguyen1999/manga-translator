try:
    import regex as re
except ImportError:
    import re

from google import genai
from google.genai import types

import asyncio
from typing import List, Optional, Any
from .common import MissingAPIKeyException, InvalidServerResponse
from .keys import GEMINI_API_KEY, GEMINI_API_KEYS, GEMINI_MODEL, GEMINI_MODELS
from .gemini_keys import GeminiKeyManager, GeminiRetryExhausted, get_current_retry_budget, mask_key
from .common_gpt import CommonGPTTranslator, _CommonGPTTranslator_JSON
from .constants import DEFAULT_GEMINI_SAFETY_SETTINGS


# Text Formatting:
# For Windows: enable ANSI escape code support
from colorama import init as initColorama

BOLD='\033[1m' # Bold text
NRML='\033[0m' # Revert to Normal formatting


def contains_cjk(text: str) -> bool:
    try:
        return bool(re.search(r'[\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Han}]', text))
    except Exception:
        return bool(re.search(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]', text))


def contains_kana(text: str) -> bool:
    try:
        return bool(re.search(r'[\p{Script=Hiragana}\p{Script=Katakana}]', text))
    except Exception:
        return bool(re.search(r'[\u3040-\u30ff]', text))


def has_letters_or_digits(text: str) -> bool:
    try:
        return bool(re.search(r'[\p{L}\p{N}]', text))
    except Exception:
        return bool(re.search(r'[a-zA-Z0-9\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]', text))


_KANA_MAP = {
    'きゃ': 'kya', 'きゅ': 'kyu', 'きょ': 'kyo',
    'しゃ': 'sha', 'しゅ': 'shu', 'しょ': 'sho',
    'ちゃ': 'cha', 'ちゅ': 'chu', 'ちょ': 'cho',
    'にゃ': 'nya', 'にゅ': 'nyu', 'にょ': 'nyo',
    'ひゃ': 'hya', 'ひゅ': 'hyu', 'ひょ': 'hyo',
    'みゃ': 'mya', 'みゅ': 'myu', 'みょ': 'myo',
    'りゃ': 'rya', 'りゅ': 'ryu', 'りょ': 'ryo',
    'ぎゃ': 'gya', 'ぎゅ': 'gyu', 'ぎょ': 'gyo',
    'じゃ': 'ja', 'じゅ': 'ju', 'じょ': 'jo',
    'びゃ': 'bya', 'びゅ': 'byu', 'びょ': 'byo',
    'ぴゃ': 'pya', 'ぴゅ': 'pyu', 'ぴょ': 'pyo',
    'あ': 'a', 'い': 'i', 'う': 'u', 'え': 'e', 'お': 'o',
    'か': 'ka', 'き': 'ki', 'く': 'ku', 'け': 'ke', 'こ': 'ko',
    'さ': 'sa', 'し': 'shi', 'す': 'su', 'せ': 'se', 'そ': 'so',
    'た': 'ta', 'ち': 'chi', 'つ': 'tsu', 'て': 'te', 'と': 'to',
    'な': 'na', 'に': 'ni', 'ぬ': 'nu', 'ね': 'ne', 'の': 'no',
    'は': 'ha', 'ひ': 'hi', 'ふ': 'fu', 'へ': 'he', 'ほ': 'ho',
    'ま': 'ma', 'み': 'mi', 'む': 'mu', 'め': 'me', 'も': 'mo',
    'や': 'ya', 'ゆ': 'yu', 'よ': 'yo',
    'ら': 'ra', 'り': 'ri', 'る': 'ru', 'れ': 're', 'ろ': 'ro',
    'わ': 'wa', 'を': 'wo', 'ん': 'n',
    'が': 'ga', 'ぎ': 'gi', 'ぐ': 'gu', 'ゲ': 'ge', 'ご': 'go',
    'ざ': 'za', 'じ': 'ji', 'ず': 'zu', 'ぜ': 'ze', 'ぞ': 'zo',
    'だ': 'da', 'ぢ': 'ji', 'づ': 'zu', 'で': 'de', 'ど': 'do',
    'ば': 'ba', 'び': 'bi', 'ぶ': 'bu', 'べ': 'be', 'ぼ': 'bo',
    'ぱ': 'pa', 'ぴ': 'pi', 'ぷ': 'pu', 'ぺ': 'pe', 'ぽ': 'po',
    'キャ': 'kya', 'キュ': 'kyu', 'キョ': 'kyo',
    'シャ': 'sha', 'シュ': 'shu', 'ショ': 'sho',
    'チャ': 'cha', 'チュ': 'chu', 'チョ': 'cho',
    'ニャ': 'nya', 'ニュ': 'nyu', 'ニョ': 'nyo',
    'ヒャ': 'hya', 'ヒュ': 'hyu', 'ヒョ': 'hyo',
    'ミャ': 'mya', 'ミュ': 'myu', 'ミョ': 'myo',
    'リャ': 'rya', 'リュ': 'ryu', 'リョ': 'ryo',
    'ギャ': 'gya', 'ギュ': 'gyu', 'ギョ': 'gyo',
    'ジャ': 'ja', 'ジュ': 'ju', 'ジョ': 'jo',
    'ビャ': 'bya', 'ビュ': 'byu', 'ビョ': 'byo',
    'ピャ': 'pya', 'ピュ': 'pyu', 'ピョ': 'pyo',
    'ア': 'a', 'イ': 'i', 'ウ': 'u', 'エ': 'e', 'オ': 'o',
    'カ': 'ka', 'キ': 'ki', 'ク': 'ku', 'ケ': 'ke', 'コ': 'ko',
    'サ': 'sa', 'シ': 'shi', 'ス': 'su', 'セ': 'se', 'ソ': 'so',
    'タ': 'ta', 'チ': 'chi', 'ツ': 'tsu', 'テ': 'te', 'ト': 'to',
    'ナ': 'na', 'ニ': 'ni', 'ヌ': 'nu', 'ネ': 'ne', 'ノ': 'no',
    'ハ': 'ha', 'ヒ': 'hi', 'フ': 'fu', 'ヘ': 'he', 'ホ': 'ho',
    'マ': 'ma', 'ミ': 'mi', 'ム': 'mu', 'メ': 'me', 'モ': 'mo',
    'ヤ': 'ya', 'ユ': 'yu', 'ヨ': 'yo',
    'ラ': 'ra', 'リ': 'ri', 'ル': 'ru', 'レ': 're', 'ロ': 'ro',
    'ワ': 'wa', 'ヲ': 'wo', 'ン': 'n',
    'ガ': 'ga', 'ギ': 'gi', 'グ': 'gu', 'ゲ': 'ge', 'ゴ': 'go',
    'ザ': 'za', 'ジ': 'ji', 'ズ': 'zu', 'ゼ': 'ze', 'ゾ': 'zo',
    'ダ': 'da', 'ヂ': 'ji', 'ヅ': 'zu', 'デ': 'de', 'ド': 'do',
    'バ': 'ba', 'ビ': 'bi', 'ブ': 'bu', 'ベ': 'be', 'ボ': 'bo',
    'パ': 'pa', 'ピ': 'pi', 'プ': 'pu', 'ペ': 'pe', 'ポ': 'po',
    'っ': '', 'ッ': '', 'ー': '-',
}

def transliterate_kana_fallback(text: str) -> str:
    res = []
    i = 0
    n = len(text)
    while i < n:
        if i + 1 < n and text[i:i+2] in _KANA_MAP:
            res.append(_KANA_MAP[text[i:i+2]])
            i += 2
        elif text[i] in _KANA_MAP:
            res.append(_KANA_MAP[text[i]])
            i += 1
        else:
            res.append(text[i])
            i += 1
    return "".join(res).strip()


_NSFW_REPLACEMENTS = [
    (r'セックス', '[intimacy]', 'sex'),
    (r'エッチ', '[intimacy]', 'h-stuff'),
    (r'ちんぽ|チンポ|ちんこ|チンコ|ペニス|肉棒', '[male organ]', 'cock'),
    (r'まんこ|マンコ|おまんこ|オマンコ|割れ目|秘部', '[female organ]', 'pussy'),
    (r'中出し|なかだし', '[inside climax]', 'creampie'),
    (r'ザーメン|精液|精子', '[fluid]', 'cum'),
    (r'射精', '[climax]', 'cumming'),
    (r'オナニー|自慰', '[self pleasure]', 'masturbation'),
    (r'フェラ(?:チオ)?', '[oral]', 'blowjob'),
    (r'パイズリ', '[chest intimacy]', 'titfuck'),
    (r'潮吹き', '[moisture]', 'squirting'),
    (r'ハメ(?:る|て|た)?', '[join]', 'fuck'),
    (r'処女', '[maiden]', 'virgin'),
    (r'童貞', '[youth]', 'virgin'),
    (r'犯す|犯され|犯すな|レイプ', '[forced intimacy]', 'violate'),
    (r'クスコ|バイブ|ローター|オナホ', '[toy]', 'toy'),
    (r'イキそう|いっちゃう|イク|いくっ', '[peak]', 'cum'),
    (r'勃起', '[erection]', 'hard-on'),
    (r'乳首|おっぱい|胸', '[chest]', 'breasts'),
]

_ADULT_PHRASE_MAP_ENG = [
    (r'ウソ[…\.、]*', 'No way... '),
    (r'うそ[…\.、]*', 'No way... '),
    (r'ホントに|本当に', 'really '),
    (r'ほんとに', 'really '),
    (r'私[…\.、\s]*セックス', "I'm... having sex"),
    (r'私|わたし', "I'm "),
    (r'僕|ぼく|俺|おれ', "I'm "),
    (r'セックスしてる[っ!]?', 'having sex!'),
    (r'セックス', 'sex'),
    (r'エッチしてる[っ!]?', 'doing it!'),
    (r'エッチ', 'lewd stuff'),
    (r'きもちいい|気持ちいい|キモチイイ', 'feels so good'),
    (r'いっちゃう|イッちゃう|イっちゃう|イク[っ!]?|イく', 'cumming!'),
    (r'中出しして[っ!]?|中に出して[っ!]?', 'cum inside me!'),
    (r'中出し|なかだし', 'creampie'),
    (r'ちんぽ|チンポ|ちんこ|チンコ|肉棒', 'cock'),
    (r'まんこ|マンコ|おまんこ|オマンコ', 'pussy'),
    (r'ザーメン|精液', 'cum'),
    (r'だめ[っ!]?|ダメ[っ!]?', 'no...!'),
    (r'もっと[っ!]?', 'more...!'),
    (r'ああっ[!]?|あっ[!]?|んっ[!]?', 'aah!'),
]

_ADULT_PHRASE_MAP_ZH = [
    (r'ウソ[…\.、]*', '骗人… '),
    (r'うそ[…\.、]*', '骗人… '),
    (r'ホントに|本当に', '真的'),
    (r'ほんとに', '真的'),
    (r'私[…\.、\s]*セックス', '我…在做爱'),
    (r'私|わたし', '我'),
    (r'僕|ぼく|俺|おれ', '我'),
    (r'セックスしてる[っ!]?', '在做爱！'),
    (r'セックス', '做爱'),
    (r'エッチしてる[っ!]?', '在做色色的事！'),
    (r'エッチ', '色色'),
    (r'きもちいい|気持ちいい|キモチイイ', '好舒服'),
    (r'いっちゃう|イッちゃう|イっちゃう|イク[っ!]?|イく', '要去了！'),
    (r'中出しして[っ!]?|中に出して[っ!]?', '射在里面！'),
    (r'中出し|なかだし', '中出'),
    (r'ちんぽ|チンポ|ちんこ|チンコ|肉棒', '肉棒'),
    (r'まんこ|マンコ|おまんこ|オマンコ', '小穴'),
    (r'ザーメン|精液', '精液'),
    (r'だめ[っ!]?|ダメ[っ!]?', '不行…！'),
    (r'もっと[っ!]?', '还要…！'),
    (r'ああっ[!]?|あっ[!]?|んっ[!]?', '啊啊！'),
]

def mask_nsfw_text(text: str) -> tuple[str, list[tuple[str, str]]]:
    masked = text
    replacements = []
    for pattern, placeholder, unmask_word in _NSFW_REPLACEMENTS:
        if re.search(pattern, masked):
            masked = re.sub(pattern, placeholder, masked)
            replacements.append((placeholder, unmask_word))
    return masked, replacements

def unmask_nsfw_text(text: str, replacements: list[tuple[str, str]]) -> str:
    res = text
    for placeholder, unmask_word in replacements:
        res = res.replace(placeholder, unmask_word)
        bare = placeholder.strip('[]')
        res = re.sub(rf'\b{re.escape(bare)}\b', unmask_word, res, flags=re.IGNORECASE)
    return res

def adult_dictionary_fallback(text: str, to_lang: str = 'en') -> str:
    res = text
    lang_upper = to_lang.upper() if to_lang else 'EN'
    is_chinese = any(k in lang_upper for k in ('CHS', 'CHT', 'ZH', 'CHINESE'))

    if is_chinese:
        for pattern, replacement in _ADULT_PHRASE_MAP_ZH:
            res = re.sub(pattern, replacement, res)
    else:
        for pattern, replacement in _ADULT_PHRASE_MAP_ENG:
            res = re.sub(pattern, replacement, res)
        if contains_kana(res):
            res = transliterate_kana_fallback(res)
        res = res.replace('…', '... ')
        res = re.sub(r'[!！]+', '!', res)
        res = re.sub(r'[?？]+', '?', res)
        res = re.sub(r'[…\.]{2,}', '...', res)
        res = re.sub(r'\s+', ' ', res).strip()
    return res

class GeminiTranslator(CommonGPTTranslator):
    _INVALID_REPEAT_COUNT = 0  # 现在这个参数没意义了
    _MAX_REQUESTS_PER_MINUTE = 9999  # 无RPM限制

    # 最大令牌数量，用于控制处理的文本长度
    # Maximum token count for controlling the length of text processed
    _MAX_TOKENS = 8192

    # 将每个 prompt 限制为最大输出 tokens 的 50％。
    # （这是一个任意比率，用于解释语言之间的差异。）
    # 
    # Limit each prompt to 50% max output tokens. 
    # (This is an arbitrary ratio to account for variance between languages.)
    _MAX_TOKENS_IN = _MAX_TOKENS // 2

    # From: https://ai.google.dev/gemini-api/docs/models/gemini#available-languages
    '''
    _LANGUAGE_CODE_MAP= {
                            'ar': 'Arabic',
                            'bn': 'Bengali',
                            'bg': 'Bulgarian',
                            'zh': 'Chinese simplified and traditional',
                            'hr': 'Croatian',
                            'cs': 'Czech',
                            'da': 'Danish',
                            'nl': 'Dutch',
                            'en': 'English',
                            'et': 'Estonian',
                            'fi': 'Finnish',
                            'fr': 'French',
                            'de': 'German',
                            'el': 'Greek',
                            'iw': 'Hebrew',
                            'hi': 'Hindi',
                            'hu': 'Hungarian',
                            'id': 'Indonesian',
                            'it': 'Italian',
                            'ja': 'Japanese',
                            'ko': 'Korean',
                            'lv': 'Latvian',
                            'lt': 'Lithuanian',
                            'no': 'Norwegian',
                            'pl': 'Polish',
                            'pt': 'Portuguese',
                            'ro': 'Romanian',
                            'ru': 'Russian',
                            'sr': 'Serbian',
                            'sk': 'Slovak',
                            'sl': 'Slovenian',
                            'es': 'Spanish',
                            'sw': 'Swahili',
                            'sv': 'Swedish',
                            'th': 'Thai',
                            'tr': 'Turkish',
                            'uk': 'Ukrainian',
                            'vi': 'Vietnamese',
                        }
    '''

    _MIN_CACHE_TOKENS = 4096 # Minimum tokens required to use Context Cache
                            # Source: https://ai.google.dev/gemini-api/docs/caching?lang=python#considerations
    
    _CACHE_TTL = 3600 # Set the Context Cache lifespan (seconds)
    _CACHE_TTL_BUFFER = 300 # Refresh the Context Cache once current time is within this many seconds of expiring

    def _init_client(self):
        model_list = None
        last_error = None
        for key in self.key_manager.get_all_keys():
            try:
                client = self.key_manager.get_genai_client(key)
                model_list = list(client.models.list())
                self.key_manager.mark_success(key)
                self._model_list = model_list
                return client
            except genai.errors.APIError as genai_err:
                last_error = genai_err
                if self.key_manager.is_invalid_key_error(genai_err):
                    self.key_manager.mark_invalid(key, str(genai_err))
                elif self.key_manager.is_rate_limit_error(genai_err):
                    self.key_manager.mark_rate_limited(key)
                else:
                    self.logger.warning(f"Error connecting with Gemini key [{mask_key(key)}]: {genai_err}")
            except Exception as e:
                last_error = e
                self.logger.warning(f"Error connecting with Gemini key [{mask_key(key)}]: {e}")

        if isinstance(last_error, genai.errors.APIError):
            raise InvalidServerResponse(
                'GEMINI_API_KEY(s) were found, but the API failed to connect.\n.' +
                f'The following error was caught:\n{last_error}'
            )
        self.logger.error(
            'GEMINI_API_KEY(s) were found, but an error was encountered during initial setup.\n.' +
            f'The following error was caught:\n{last_error}'
        )
        if last_error:
            raise last_error
        raise InvalidServerResponse('Failed to initialize Gemini API with provided keys.')

    def _configure_model_capabilities(self):
        model_list = getattr(self, '_model_list', None)
        model_names = [aModel.name.lstrip('models/') for aModel in model_list] if model_list else []
        self._caching_supported_models = set()
        if model_list:
            for aModel in model_list:
                m_name = aModel.name.lstrip('models/')
                if 'createCachedContent' in getattr(aModel, 'supported_actions', []):
                    self._caching_supported_models.add(m_name)

        all_models = self.key_manager.get_all_models()
        unrecognized = [m for m in all_models if m not in model_names]
        if unrecognized:
            self.logger.info(
                f"Gemini model(s) {unrecognized} not returned in models.list(). "
                "They will still be attempted dynamically during translation rotation."
            )

        model_info = None
        if GEMINI_MODEL in model_names:
            model_info = model_list[model_names.index(GEMINI_MODEL)]
        else:
            for m in all_models:
                if m in model_names:
                    model_info = model_list[model_names.index(m)]
                    break

        if model_info:
            self._canUseCache = ('createCachedContent' in getattr(model_info, 'supported_actions', []))
            self._MAX_TOKENS = getattr(model_info, 'output_token_limit', 8192) or 8192
        else:
            self._canUseCache = False
            self._MAX_TOKENS = 8192

        self._MAX_TOKENS_IN = self._MAX_TOKENS // 2

    def __init__(self):
        # ConfigGPT 的初始化
        # ConfigGPT initialization 
        _CONFIG_KEY = 'gemini.' + GEMINI_MODEL
        CommonGPTTranslator.__init__(self, config_key=_CONFIG_KEY)

        # Initialize colorama for ANSI encoding support
        #   (Only required on Windows)
        initColorama()

        # By default: Do not assume Context Cache support
        self._canUseCache = False
        self.cached_content = None
        self.templateCache = None

        # Dict for storing values to print to logger
        self.cachedVals = {}

        if not GEMINI_API_KEYS and not GEMINI_API_KEY:
            raise MissingAPIKeyException(
                        'Please set the GEMINI_API_KEY (or GEMINI_API_KEYS) environment variable '
                        'before using the Gemini translator.'
                    )

        initial_keys = GEMINI_API_KEYS if GEMINI_API_KEYS else [GEMINI_API_KEY]
        initial_models = GEMINI_MODELS if GEMINI_MODELS else [GEMINI_MODEL]
        self.key_manager = GeminiKeyManager(initial_keys, models=initial_models, logger_instance=self.logger)

        self.client = self._init_client()
        self._configure_model_capabilities()

        self.safety_settings = DEFAULT_GEMINI_SAFETY_SETTINGS
        self.token_count = 0
        self.token_count_last = 0 
        self.config = None
        self._parse_response = self._parse_response_standard

    @property
    def useCache(self) -> bool:
        """
        Whether or not to use Context Caching.

        Gemini 2.0 and later models appear to have a minimum token requirement for context caching.
        If the model supports caching: attempt to use caching.
        If caching fails: The user is informed and caching is disabled.


        Returns:
            bool: True if context caching is supported & cache was successfully created
                  False otherwise.
        """

        if self._canUseCache:
            try:
                if self._needRecache:
                    self._createContext(to_lang=self.to_lang)
                
                return True
            
            except Exception as e:
                self.logger.warning(
                    f"\nContext Cache is supported on this model, but the cache could not be created.\n"
                    f"The following error was encountered when attempting to create Context Cache:\n{e}\n\n"
                    f"The most likely cause is that context contents (`System Prompt` + `Chat Samples`) does not the meet the minimum token length for the model.\n"
                    "Context Caching will be disabled. If you wish to use caching: Try using Gemini 1.5 or increase `System Prompt` and/or `Chat Sample` size."
                )
                self._canUseCache = False

        return False

    def canModelCache(self, model: str) -> bool:
        if hasattr(self, '_caching_supported_models') and self._caching_supported_models:
            return model in self._caching_supported_models
        return self._canUseCache

    def parse_args(self, args: CommonGPTTranslator):
        super().parse_args(args)
        
        # Add custom keys/models from gpt_config if defined
        if self.config:
            custom_keys = self.config.get('api_keys') or self.config.get('api_key')
            if custom_keys:
                added = self.key_manager.add_keys(custom_keys)
                if added:
                    self.logger.info(f"Loaded {added} additional Gemini API key(s) from gpt_config.")
            custom_models = self.config.get('models') or self.config.get('model')
            if custom_models:
                added_m = self.key_manager.add_models(custom_models)
                if added_m:
                    self.logger.info(f"Loaded {added_m} additional Gemini model(s) from gpt_config.")

        # Initialize mode-specific components AFTER config is loaded
        if self.json_mode:
            self._init_json_mode()
        else:
            self._init_standard_mode()

    def _init_json_mode(self):
        """Activate JSON-specific behavior"""
        self._json_funcs = _GeminiTranslator_json(self)

        self._createContext = self._json_funcs._createContext
        self._request_translation = self._json_funcs._request_translation
        self._assemble_prompts = self._json_funcs._assemble_prompts
        self._parse_response = self._json_funcs._parse_response

    def _init_standard_mode(self):
        """Use default method implementations"""
        self._assemble_prompts = super()._assemble_prompts
        self._parse_response = self._parse_response_standard

    def _parse_response_standard(self, response: str, queries: List[str]) -> List[str]:
        if not response:
            return []

        clean_resp = response.strip()
        # Strip markdown codeblocks if model wrapped response in ```...```
        if clean_resp.startswith("```"):
            clean_resp = re.sub(r'^```[a-zA-Z]*\n?', '', clean_resp)
            clean_resp = re.sub(r'\n?```$', '', clean_resp).strip()

        # If <|ID|> tags exist in the response
        if re.search(r'<\|\d+\|>', clean_resp):
            parts = re.split(r'<\|\d+\|>', clean_resp)
            if parts and not parts[0].strip():
                parts = parts[1:]
            parsed = [re.sub(r'^\s*<\|\d+\|>\s*', '', p).strip() for p in parts]
            if parsed:
                return parsed

        # If no <|ID|> tags found and only 1 query was asked:
        if len(queries) == 1 and clean_resp:
            return [clean_resp]

        # Line-by-line fallback
        lines = [line.strip() for line in clean_resp.split('\n') if line.strip()]
        lines = [re.sub(r'^\s*(?:<\|\d+\|>|\d+[\.:\)]\s*)\s*', '', line).strip() for line in lines]
        return lines


    def count_tokens(self, text: str) -> int:
        # Uses the synchronous call (`client`) instead of asynchronous (`client.aio`)
        #   for compatibility with `common_gpt` 's `assemble_prompt`
        client = self.client or self.key_manager.get_genai_client()
        model = self.key_manager.current_model or GEMINI_MODEL
        try:
            return client.models.count_tokens(model=model, contents=text).total_tokens
        except Exception:
            try:
                return client.models.count_tokens(model=GEMINI_MODEL, contents=text).total_tokens
            except Exception:
                return len(text)
    
    def get_or_create_cache_for_key(self, key: str, client: Any, to_lang: str, model: Optional[str] = None) -> Any:
        use_model = model or self.key_manager.current_model or GEMINI_MODEL
        cache = self.key_manager.get_cached_content(key, model=use_model)
        need_create = False
        if cache is None:
            need_create = True
        else:
            try:
                delta = cache.expire_time.timestamp() - cache.expire_time.now().timestamp()
                if delta < self._CACHE_TTL_BUFFER:
                    need_create = True
            except Exception:
                need_create = True

        if need_create:
            chatSamples = None
            sysTemplate = self.chat_system_template.format(to_lang=to_lang)
            self.cachedVals = {'System Prompt (Cached)': sysTemplate}

            lang_chat_samples = self.get_chat_sample(to_lang)
            if lang_chat_samples:
                chatSamples = [
                    types.Content(role='user', parts=[types.Part.from_text(text=lang_chat_samples[0])]),
                    types.Content(role='model', parts=[types.Part.from_text(text=lang_chat_samples[1])]),
                ]
                self.cachedVals['Sample (Cached): User'] = lang_chat_samples[0]
                self.cachedVals['Sample (Cached): Model'] = lang_chat_samples[1]

            get_current_retry_budget().consume()
            cache = client.caches.create(
                model=use_model,
                config=types.CreateCachedContentConfig(
                    contents=chatSamples,
                    system_instruction=sysTemplate,
                    display_name=f'TranslationCache_{use_model}',
                    ttl=f'{self._CACHE_TTL}s',
                ),
            )
            self.key_manager.set_cached_content(key, cache, model=use_model)
            self.templateCache = cache

        return cache

    def _createContext(self, to_lang: str): 
        key = self.key_manager.get_next_key(allow_cooldown=True)
        model = self.key_manager.get_next_model(allow_cooldown=True) or GEMINI_MODEL
        client = self.key_manager.get_genai_client(key) if key else self.client
        return self.get_or_create_cache_for_key(key, client, to_lang, model=model)
        
    def _needRecache(self) -> bool:
        if self.templateCache is None:
            return True

        # expire_time (as seconds) - now (as seconds)
        delta = (
                    # Get expire_time as unix timestamp
                    self.templateCache.expire_time.timestamp()
                    -
                    # Access `datetime.datetime` library through through the variable. 
                    # Get current time as unixtimestamp
                    self.templateCache.expire_time.now().timestamp()
            )
        
        # If cache expire_time is less than 5 minutes (300 seconds) in the future: return True
        return delta < self._CACHE_TTL_BUFFER

    def _clean_single_response(self, resp: str, q: str, to_lang: str) -> Optional[str]:
        if not resp:
            return None
        resp = re.sub(r'^```[a-zA-Z]*\n?', '', resp)
        resp = re.sub(r'\n?```$', '', resp).strip()
        resp = re.sub(r'^\s*<\|\d+\|>\s*', '', resp).strip()
        resp = re.sub(r'^(?:Translation|Translated|Result):\s*', '', resp, flags=re.IGNORECASE)
        numeric_prefix = re.compile(r'^(?:Page\s+)?1\s*[.:)]\s*', re.IGNORECASE)
        if numeric_prefix.match(resp) and not re.match(r'^\s*(?:Page\s+)?1\s*[.:)](?:\s|$)', q, re.IGNORECASE):
            resp = numeric_prefix.sub('', resp, count=1)
        resp = re.sub(r'^["\'「『]|["\'」』]$', '', resp).strip()
        resp = " ".join(line.strip() for line in resp.splitlines() if line.strip())
        if not resp:
            return None
        is_unchanged_cjk = (
            q.casefold() == resp.casefold()
            and contains_cjk(q)
            and to_lang.upper() not in ('JPN', 'JA', 'JAPANESE', 'CHS', 'CHT', 'ZH', 'CHINESE', 'KOR', 'KO', 'KOREAN')
        )
        if is_unchanged_cjk:
            return None
        return resp

    async def _translate_single_fallback(self, query: str, to_lang: str) -> str:
        q = query.strip()
        if not q:
            return ""

        # If text is purely punctuation or symbols without letters or digits (e.g. ..., !?, ---)
        if not has_letters_or_digits(q):
            return q

        # Keep the final fallback local so a bad batch cannot fan out into
        # another API request for every region.
        _, replacements = mask_nsfw_text(q)
        adult_fallback = adult_dictionary_fallback(q, to_lang=to_lang)
        if adult_fallback and adult_fallback != q and not (contains_cjk(q) and adult_fallback.strip().casefold() == q.casefold()):
            self.logger.info(f"Used dictionary adult fallback for query '{q}' -> '{adult_fallback}'")
            return adult_fallback

        if (contains_kana(q)
                and to_lang.upper() not in ('JPN', 'JA', 'JAPANESE', 'CHS', 'CHT', 'ZH', 'CHINESE', 'KOR', 'KO', 'KOREAN')):
            transliterated = transliterate_kana_fallback(q)
            if transliterated and transliterated != q:
                self.logger.info(f"Transliterated untranslated kana for query '{q}' -> '{transliterated}'")
                return transliterated

        if replacements:
            self.logger.warning(f"Gemini could not translate protected content locally: '{q}'")
        return q

    def _clean_batch_translations(self, raw_translations: List[str], query_size: int) -> List[str]:
        trimmed = raw_translations[:query_size] + [''] * (query_size - len(raw_translations))
        cleaned = []
        for t in trimmed:
            t = re.sub(r'^\s*<\|\d+\|>\s*', '', t).strip()
            t = " ".join(line.strip() for line in t.splitlines() if line.strip())
            cleaned.append(t)
        return cleaned

    def _check_translation_issue(self, prompt_queries: List[str], new_translations: List[str], to_lang: str) -> Optional[str]:
        if any(not t.strip() and q.strip() for q, t in zip(prompt_queries, new_translations)):
            return "Empty translations"

        if to_lang.upper() not in ('JPN', 'JA', 'JAPANESE', 'CHS', 'CHT', 'ZH', 'CHINESE', 'KOR', 'KO', 'KOREAN'):
            for q, t in zip(prompt_queries, new_translations):
                if q.strip().casefold() == t.strip().casefold() and contains_cjk(q):
                    return "Untranslated CJK dialogue"
        return None

    async def _handle_batch_split_or_fallback(
        self,
        from_lang: str,
        to_lang: str,
        prompt_queries: List[str],
        prompt_query_indices: List[int],
        split_level: int,
        max_split_attempts: int,
        translations: List[str],
        total_queries: int,
    ) -> bool:
        budget = get_current_retry_budget()
        if split_level < min(max_split_attempts, 1) and len(prompt_queries) > 1 and budget.claim_retry("split"):
            self.logger.warning('Gemini batch response remained invalid. Splitting once before failing.')
            mid_index = len(prompt_queries) // 2
            futures = []
            for sub_queries, sub_indices in [
                (prompt_queries[:mid_index], prompt_query_indices[:mid_index]),
                (prompt_queries[mid_index:], prompt_query_indices[mid_index:]),
            ]:
                if sub_queries:
                    futures.append(self._translate_batch(
                        from_lang, to_lang, sub_queries, sub_indices, translations, total_queries, split_level + 1, max_split_attempts
                    ))
            results = await asyncio.gather(*futures)
            return all(results)

        self.logger.warning(
            f'Batch of size {len(prompt_queries)} remains invalid. Using local fallback only.'
        )
        all_ok = True
        for idx, q in zip(prompt_query_indices, prompt_queries):
            if translations[idx] and translations[idx].strip():
                continue
            trans = await self._translate_single_fallback(q, to_lang)
            translations[idx] = trans
            if not trans or not trans.strip():
                all_ok = False
        return all_ok

    async def _translate_batch(
        self,
        from_lang: str,
        to_lang: str,
        prompt_queries: List[str],
        prompt_query_indices: List[int],
        translations: List[str],
        total_queries: int,
        split_level: int = 0,
        max_split_attempts: int = 5,
    ) -> bool:
        prompt, query_size = self._assemble_prompts(from_lang, to_lang, prompt_queries).__next__()
        budget = get_current_retry_budget()

        for output_attempt in range(2):
            request_prompt = prompt
            if output_attempt:
                request_prompt += (
                    f"\n\nCorrection: return exactly {query_size} translations using "
                    "the requested numbered tags, with no commentary."
                )
            response = await self._request_translation(to_lang, request_prompt)
            try:
                new_translations = self._parse_response(response, prompt_queries)
            except Exception as e:
                self.logger.warning(f'Gemini response parsing failed: {e}')
                new_translations = []

            if not isinstance(new_translations, (list, tuple)):
                new_translations = []

            if len(new_translations) < query_size:
                new_translations = [line.strip() for line in response.strip().split('\n') if line.strip()]

            issue = None
            if len(new_translations) < query_size:
                issue = f'Incomplete response ({len(new_translations)}/{query_size})'
            else:
                new_translations = self._clean_batch_translations(new_translations, query_size)
                issue = self._check_translation_issue(prompt_queries, new_translations, to_lang)

            if issue is None:
                for idx, translation in zip(prompt_query_indices, new_translations):
                    translations[idx] = translation

                self.logger.info(f'Batch translated: {len([t for t in translations if t])}/{total_queries} completed.')
                self.logger.debug(f'Completed translations: {translations}')
                return True

            self.logger.warning(f'{issue}; output attempt {output_attempt + 1}/2.')
            if output_attempt == 0 and budget.claim_retry("output"):
                self.logger.warning('Retrying Gemini with one corrective output request.')
                continue
            break

        return await self._handle_batch_split_or_fallback(
            from_lang, to_lang, prompt_queries, prompt_query_indices, split_level, max_split_attempts, translations, total_queries
        )

    async def _finalize_translations(self, queries: List[str], to_lang: str, translations: List[str]):
        is_target_cjk = to_lang.upper() in ('JPN', 'JA', 'JAPANESE', 'CHS', 'CHT', 'ZH', 'CHINESE', 'KOR', 'KO', 'KOREAN')
        for i, q in enumerate(queries):
            if not q.strip():
                continue
            if not translations[i] or not translations[i].strip():
                self.logger.warning(f"Query {i} ('{q}') left empty after translation. Applying fallback...")
                translations[i] = await self._translate_single_fallback(q, to_lang)
            elif not is_target_cjk and translations[i].strip().casefold() == q.strip().casefold() and contains_cjk(q):
                self.logger.warning(f"Query {i} ('{q}') left as unchanged CJK. Applying fallback...")
                translations[i] = await self._translate_single_fallback(q, to_lang)

    async def _translate(self, from_lang: str, to_lang: str, queries: List[str]) -> List[str]:  
        self.to_lang = to_lang
        translations = [''] * len(queries)  
        self.logger.debug(f'Temperature: {self.temperature}, TopP: {self.top_p}')  

        prompt_queries = queries  
        prompt_query_indices = list(range(len(queries)))  
        await self._translate_batch(from_lang, to_lang, prompt_queries, prompt_query_indices, translations, len(queries))

        await self._finalize_translations(queries, to_lang, translations)

        self.logger.debug(translations)  
        if self.token_count_last:  
            self.logger.info(f'Used {self.token_count_last} tokens (Total: {self.token_count})')  
        return translations

    def formatLog(self, vals: dict) -> str:
        return '\n---\n'.join(f"\n{BOLD}{aKey}{NRML}:\n{aVal}" 
                                for aKey, aVal in vals.items()
                            )

    async def _select_request_target(self, attempt: int, total_targets: int):
        key, model = self.key_manager.get_next_target(allow_cooldown=False)
        if not key or not model:
            raise GeminiRetryExhausted("No healthy Gemini API keys/models are available without waiting.")

        return key, model

    def _extract_response_text_and_usage(self, response) -> str:
        if not hasattr(response, 'usage_metadata') or response.usage_metadata is None:
            self.logger.warning("Response does not contain usage information")
            self.token_count_last = 0
        else:
            self.token_count += getattr(response.usage_metadata, 'prompt_token_count', 0) or 0
            self.token_count_last = getattr(response.usage_metadata, 'total_token_count', 0) or 0

        try:
            resp_text = response.text
        except Exception:
            resp_text = None

        if resp_text is None and hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, 'content') and candidate.content:
                parts = getattr(candidate.content, 'parts', None) or []
                parts_text = [getattr(p, 'text', '') for p in parts if getattr(p, 'text', None)]
                if parts_text:
                    resp_text = "".join(parts_text)
            if resp_text is None:
                finish_reason = getattr(candidate, 'finish_reason', 'UNKNOWN')
                self.logger.warning(f"Gemini response has no text content (finish_reason: {finish_reason}).")

        return resp_text if resp_text is not None else ""

    async def _handle_request_error(
        self, ex: Exception, key: str, model: str, budget, mode_label: str = ""
    ):
        if self.key_manager.is_rate_limit_error(ex):
            self.key_manager.mark_rate_limited(
                key,
                model=model if self.key_manager.is_model_rate_limit_error(ex) else None,
            )
            budget.rate_limited_keys.add(key)
            self.logger.warning(
                f"Gemini target [key: {mask_key(key)}, model: {model}] rate limit/quota hit{mode_label}. "
                "Cooling down key and rotating once..."
            )
            return
        if self.key_manager.is_model_not_found_error(ex):
            self.key_manager.mark_model_invalid(model, str(ex))
            self.logger.warning(
                f"Gemini model [{model}] not found or unsupported{mode_label} ({ex}). "
                f"Disabling model and jumping to next target..."
            )
            return
        if self.key_manager.is_invalid_key_error(ex):
            self.key_manager.mark_invalid(key, str(ex))
            self.logger.error(
                f"Gemini API key [{mask_key(key)}] is invalid ({ex}). Disabling key and rotating..."
            )
            return

        if budget.claim_retry("transient"):
            self.logger.warning(
                f"Transient Gemini error{mode_label} for target [key: {mask_key(key)}, model: {model}]. "
                "Retrying once after 1s."
            )
            await asyncio.sleep(1)
            return

        raise ex

    def _build_chat_request_payload(self, key: str, model: str, client, to_lang: str, prompt: str):
        config_kwargs = {
            'safety_settings': self.safety_settings,
            'top_p': self.top_p,
            'temperature': self.temperature,
        }
        if getattr(self, "_professional_json_mode", False):
            config_kwargs['response_mime_type'] = 'application/json'

        messages = []
        loggerVals = {}
        use_cached = False
        if self._canUseCache and self.canModelCache(model):
            try:
                cache = self.get_or_create_cache_for_key(key, client, to_lang, model=model)
                if cache:
                    config_kwargs['cached_content'] = cache.name
                    loggerVals = self.cachedVals.copy()
                    use_cached = True
            except Exception as e:
                if isinstance(e, GeminiRetryExhausted) or self.key_manager.is_rate_limit_error(e):
                    raise
                self.logger.warning(
                    f"Context Cache creation failed for key [{mask_key(key)}] model [{model}]: {e}. "
                    "Falling back to un-cached request."
                )
                use_cached = False

        if not use_cached:
            config_kwargs['system_instruction'] = self.chat_system_template.format(to_lang=to_lang)
            loggerVals = {'System Prompt': config_kwargs['system_instruction']}

            lang_chat_samples = self.get_chat_sample(to_lang)
            if lang_chat_samples:
                messages = [
                    types.Content(role='user', parts=[types.Part.from_text(text=lang_chat_samples[0])]),
                    types.Content(role='model', parts=[types.Part.from_text(text=lang_chat_samples[1])])
                ]
                loggerVals['Sample: User'] = lang_chat_samples[0]
                loggerVals['Sample: Model'] = lang_chat_samples[1]

        messages.append(types.Content(role='user', parts=[types.Part.from_text(text=prompt)]))
        loggerVals['Input'] = prompt

        self.logger.debug(
            f'-- GPT Prompt (Key: {mask_key(key)}, Model: {model}) --\n' +
            self.formatLog(loggerVals) +
            '\n------------'
        )
        return config_kwargs, messages

    async def _request_translation(self, to_lang: str, prompt: str) -> str:
        budget = get_current_retry_budget()

        while True:
            key, model = await self._select_request_target(budget.used, self.key_manager.valid_targets_count)
            client = self.key_manager.get_genai_client(key)
            self.client = client

            try:
                config_kwargs, messages = self._build_chat_request_payload(key, model, client, to_lang, prompt)
                budget.consume()
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=messages,
                    config=types.GenerateContentConfig(**config_kwargs)
                )

                self.key_manager.mark_success(key, model=model)
                resp_str = self._extract_response_text_and_usage(response)
                log_text = resp_str if resp_str else "(empty response)"
                self.logger.debug(f'-- GPT Response (Key: {mask_key(key)}, Model: {model}) --\n{log_text}')
                return resp_str

            except GeminiRetryExhausted:
                raise
            except Exception as ex:
                await self._handle_request_error(ex, key, model, budget)



class _GeminiTranslator_json (_CommonGPTTranslator_JSON):
    from .config_gpt import TranslationList
    import json

    """Internal helper class for JSON mode logic"""
    def __init__(self, translator: GeminiTranslator):
        super().__init__(translator)
        self.translator = translator

        # For conveniance: Simplify logger calls:
        self.logger = self.translator.logger 

    def get_or_create_cache_for_key(self, key: str, client: Any, to_lang: str, model: Optional[str] = None) -> Any:
        use_model = model or self.translator.key_manager.current_model or GEMINI_MODEL
        cache = self.translator.key_manager.get_cached_content(key, model=use_model)
        need_create = False
        if cache is None:
            need_create = True
        else:
            try:
                delta = cache.expire_time.timestamp() - cache.expire_time.now().timestamp()
                if delta < self.translator._CACHE_TTL_BUFFER:
                    need_create = True
            except Exception:
                need_create = True

        if need_create:
            JSON_Samples = []
            sysTemplate = self.translator.chat_system_template.format(to_lang=to_lang)
            self.cachedVals = {'System Prompt (Cached)': sysTemplate}

            lang_JSON_samples = self.translator.get_json_sample(to_lang)
            if lang_JSON_samples:
                JSON_Samples = [
                    types.Content(role='user', parts=[types.Part.from_text(text=lang_JSON_samples[0].model_dump_json())]),
                    types.Content(role='model', parts=[types.Part.from_text(text=lang_JSON_samples[1].model_dump_json())]),
                ]
                self.cachedVals['Sample (Cached): User'] = self.ppJSON(lang_JSON_samples[0].model_dump_json())
                self.cachedVals['Sample (Cached): Model'] = self.ppJSON(lang_JSON_samples[1].model_dump_json())

            get_current_retry_budget().consume()
            cache = client.caches.create(
                model=use_model,
                config=types.CreateCachedContentConfig(
                    contents=JSON_Samples,
                    system_instruction=sysTemplate,
                    display_name=f'TranslationCache_JSON_{use_model}',
                    ttl=f'{self.translator._CACHE_TTL}s',
                ),
            )
            self.translator.key_manager.set_cached_content(key, cache, model=use_model)
            self.templateCache = cache

        return cache

    def _createContext(self, to_lang: str):
        key = self.translator.key_manager.get_next_key(allow_cooldown=True)
        model = self.translator.key_manager.get_next_model(allow_cooldown=True) or GEMINI_MODEL
        client = self.translator.key_manager.get_genai_client(key) if key else self.translator.client
        return self.get_or_create_cache_for_key(key, client, to_lang, model=model)

    def _build_json_request_payload(self, key: str, model: str, client, to_lang: str, prompt: str):
        config_kwargs = {
            'safety_settings': self.translator.safety_settings,
            'response_mime_type': 'application/json',
            'response_schema': self.TranslationList,
            'top_p': self.translator.top_p,
            'temperature': self.translator.temperature,
        }

        messages = []
        loggerVals = {}
        use_cached = False
        if self.translator._canUseCache and self.translator.canModelCache(model):
            try:
                cache = self.get_or_create_cache_for_key(key, client, to_lang, model=model)
                if cache:
                    config_kwargs['cached_content'] = cache.name
                    loggerVals = self.cachedVals.copy()
                    use_cached = True
            except Exception as e:
                if isinstance(e, GeminiRetryExhausted) or self.translator.key_manager.is_rate_limit_error(e):
                    raise
                self.logger.warning(
                    f"Context Cache creation failed for key [{mask_key(key)}] model [{model}]: {e}. "
                    "Falling back to un-cached request."
                )
                use_cached = False

        if not use_cached:
            config_kwargs['system_instruction'] = self.translator.chat_system_template.format(to_lang=to_lang)
            loggerVals = {'System Prompt': config_kwargs['system_instruction']}

            lang_JSON_samples = self.translator.get_json_sample(to_lang)
            if lang_JSON_samples:
                messages = [
                    types.Content(role='user', parts=[types.Part.from_text(text=lang_JSON_samples[0].model_dump_json())]),
                    types.Content(role='model', parts=[types.Part.from_text(text=lang_JSON_samples[1].model_dump_json())]),
                ]
                loggerVals['Sample: User'] = lang_JSON_samples[0].model_dump_json()
                loggerVals['Sample: Model'] = lang_JSON_samples[1].model_dump_json()

        messages.append(types.Content(role='user', parts=[types.Part.from_text(text=prompt)]))
        loggerVals['Input'] = self.ppJSON(prompt)

        self.logger.debug(
            f'-- GPT Prompt (Key: {mask_key(key)}, Model: {model}) --\n' +
            self.translator.formatLog(loggerVals) +
            '\n------------'
        )
        return config_kwargs, messages

    async def _request_translation(self, to_lang: str, prompt: str) -> str:
        budget = get_current_retry_budget()

        while True:
            key, model = await self.translator._select_request_target(
                budget.used, self.translator.key_manager.valid_targets_count
            )
            client = self.translator.key_manager.get_genai_client(key)
            self.translator.client = client

            try:
                config_kwargs, messages = self._build_json_request_payload(key, model, client, to_lang, prompt)
                budget.consume()
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=messages,
                    config=types.GenerateContentConfig(**config_kwargs)
                )

                self.translator.key_manager.mark_success(key, model=model)
                resp_str = self.translator._extract_response_text_and_usage(response)

                try:
                    log_text = self.ppJSON(resp_str) if resp_str else "(empty response)"
                except Exception:
                    log_text = resp_str
                self.logger.debug(
                    f'-- GPT Response (Key: {mask_key(key)}, Model: {model}) --\n' + 
                    log_text + 
                    '\n------------\n'
                )
                return resp_str

            except GeminiRetryExhausted:
                raise
            except Exception as ex:
                await self.translator._handle_request_error(ex, key, model, budget, mode_label=" in JSON mode")
