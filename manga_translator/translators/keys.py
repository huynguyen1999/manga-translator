import os
from dotenv import load_dotenv
load_dotenv()

# baidu & youdao & deepl
BAIDU_APP_ID, BAIDU_SECRET_KEY = os.getenv('BAIDU_APP_ID', ''), os.getenv('BAIDU_SECRET_KEY', '')
YOUDAO_APP_KEY, YOUDAO_SECRET_KEY = os.getenv('YOUDAO_APP_KEY', ''), os.getenv('YOUDAO_SECRET_KEY', '')
DEEPL_AUTH_KEY = os.getenv('DEEPL_AUTH_KEY', '')
# openai
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-5.4-mini')
OPENAI_HTTP_PROXY = os.getenv('OPENAI_HTTP_PROXY')
OPENAI_GLOSSARY_PATH = os.getenv('OPENAI_GLOSSARY_PATH', './dict/mit_glossary.txt')
OPENAI_API_BASE = os.getenv('OPENAI_API_BASE', 'https://api.openai.com/v1')

# groq
try:
    from .groq_keys import parse_groq_keys, parse_groq_models
except (ImportError, ModuleNotFoundError):
    from groq_keys import parse_groq_keys, parse_groq_models

GROQ_API_KEYS = parse_groq_keys([os.getenv('GROQ_API_KEYS', ''), os.getenv('GROQ_API_KEY', '')])
GROQ_API_KEY = GROQ_API_KEYS[0] if GROQ_API_KEYS else ''
_env_groq_models, _env_groq_model = os.getenv('GROQ_MODELS', ''), os.getenv('GROQ_MODEL', '')
if _env_groq_models:
    GROQ_MODELS = parse_groq_models(_env_groq_models)
elif _env_groq_model:
    GROQ_MODELS = parse_groq_models(_env_groq_model)
else:
    GROQ_MODELS = ['mixtral-8x7b-32768']
GROQ_MODEL = GROQ_MODELS[0] if GROQ_MODELS else 'mixtral-8x7b-32768'

# sakura & caiyun
SAKURA_API_BASE = os.getenv('SAKURA_API_BASE', 'http://127.0.0.1:8080/v1')
SAKURA_VERSION = os.getenv('SAKURA_VERSION', '0.9')
SAKURA_DICT_PATH = os.getenv('SAKURA_DICT_PATH', './dict/sakura_dict.txt')
CAIYUN_TOKEN = os.getenv('CAIYUN_TOKEN', '')

# Gemini
try:
    from .gemini_keys import parse_gemini_keys, parse_gemini_models
except (ImportError, ModuleNotFoundError):
    from gemini_keys import parse_gemini_keys, parse_gemini_models
GEMINI_API_KEYS = parse_gemini_keys([os.getenv('GEMINI_API_KEYS', ''), os.getenv('GEMINI_API_KEY', '')])
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ''
_env_gemini_models, _env_gemini_model = os.getenv('GEMINI_MODELS', ''), os.getenv('GEMINI_MODEL', '')
if _env_gemini_models:
    GEMINI_MODELS = parse_gemini_models(_env_gemini_models)
elif _env_gemini_model:
    GEMINI_MODELS = parse_gemini_models(_env_gemini_model)
else:
    GEMINI_MODELS = ['gemini-1.5-flash-002']
GEMINI_MODEL = GEMINI_MODELS[0] if GEMINI_MODELS else 'gemini-1.5-flash-002'

# deepseek
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', '')
DEEPSEEK_API_BASE = os.getenv('DEEPSEEK_API_BASE', 'https://api.deepseek.com')
DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')

# Together AI
TOGETHER_API_KEY = os.getenv('TOGETHER_API_KEY', '')
TOGETHER_VL_MODEL = os.getenv('TOGETHER_VL_MODEL', 'Qwen/Qwen2.5-VL-72B-Instruct')

# ollama
CUSTOM_OPENAI_API_KEY = os.getenv('CUSTOM_OPENAI_API_KEY', 'ollama')
CUSTOM_OPENAI_API_BASE = os.getenv('CUSTOM_OPENAI_API_BASE', 'http://localhost:11434/v1')
CUSTOM_OPENAI_MODEL = os.getenv('CUSTOM_OPENAI_MODEL', '')
CUSTOM_OPENAI_MODEL_CONF = os.getenv('CUSTOM_OPENAI_MODEL_CONF', '')

# OpenRouter
OPENROUTER_MODELS = {'qwen': 'qwen/qwen3.8-27b:free', 'inkling': 'thinking-machines/inkling:free', 'nemotron_ultra': 'nvidia/nemotron-3-ultra-550b-a55b:free', 'nemotron_lightning': 'nvidia/nemotron-3.5-lightning:free'}
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
OPENROUTER_API_BASE = os.getenv('OPENROUTER_API_BASE', 'https://openrouter.ai/api/v1')
OPENROUTER_MODEL = OPENROUTER_MODELS.get(os.getenv('OPENROUTER_MODEL', OPENROUTER_MODELS['qwen']), os.getenv('OPENROUTER_MODEL', OPENROUTER_MODELS['qwen']))
OPENROUTER_PROVIDER_SORT = os.getenv('OPENROUTER_PROVIDER_SORT', 'price')

# Token Harbor
TOKEN_HARBOR_API_KEY = os.getenv('TOKEN_HARBOR_API_KEY', '')
TOKEN_HARBOR_API_BASE = os.getenv('TOKEN_HARBOR_API_BASE', 'https://tokenharbor.ai/v1')
TOKEN_HARBOR_MODEL = os.getenv('TOKEN_HARBOR_MODEL', 'deepseek-v4.1-flash:free')

# Dash LLM / DashScope
DASHSCOPE_API_KEY = os.getenv('DASHSCOPE_API_KEY', os.getenv('DASH_API_KEY', ''))
DASH_API_KEY = DASHSCOPE_API_KEY
DASHSCOPE_API_BASE = os.getenv('DASHSCOPE_API_BASE', os.getenv('DASH_API_BASE', 'https://dashscope.aliyuncs.com/compatible-mode/v1'))
DASH_API_BASE = DASHSCOPE_API_BASE
DASHSCOPE_MODEL = os.getenv('DASHSCOPE_MODEL', os.getenv('DASH_MODEL', 'deepseek-v3'))
DASH_MODEL = DASHSCOPE_MODEL
