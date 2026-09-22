import os
from dotenv import load_dotenv
load_dotenv()

# baidu
BAIDU_APP_ID = os.getenv('BAIDU_APP_ID', '') #你的appid
BAIDU_SECRET_KEY = os.getenv('BAIDU_SECRET_KEY', '') #你的密钥
# youdao
YOUDAO_APP_KEY = os.getenv('YOUDAO_APP_KEY', '') # 应用ID
YOUDAO_SECRET_KEY = os.getenv('YOUDAO_SECRET_KEY', '') # 应用秘钥
# deepl
DEEPL_AUTH_KEY = os.getenv('DEEPL_AUTH_KEY', '') #YOUR_AUTH_KEY
# openai
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-5.4-mini')

# groq
try:
    from .groq_keys import parse_groq_keys, parse_groq_models
except (ImportError, ModuleNotFoundError):
    from groq_keys import parse_groq_keys, parse_groq_models

GROQ_API_KEYS = parse_groq_keys([os.getenv('GROQ_API_KEYS', ''), os.getenv('GROQ_API_KEY', '')])
GROQ_API_KEY = GROQ_API_KEYS[0] if GROQ_API_KEYS else ''

_env_groq_models = os.getenv('GROQ_MODELS', '')
_env_groq_model = os.getenv('GROQ_MODEL', '')
if _env_groq_models:
    GROQ_MODELS = parse_groq_models(_env_groq_models)
elif _env_groq_model:
    GROQ_MODELS = parse_groq_models(_env_groq_model)
else:
    GROQ_MODELS = ['mixtral-8x7b-32768']

GROQ_MODEL = GROQ_MODELS[0] if GROQ_MODELS else 'mixtral-8x7b-32768'

OPENAI_HTTP_PROXY = os.getenv('OPENAI_HTTP_PROXY') # TODO: Replace with --proxy
OPENAI_GLOSSARY_PATH = os.getenv('OPENAI_GLOSSARY_PATH', './dict/mit_glossary.txt') # OpenAI术语表路径
OPENAI_API_BASE = os.getenv('OPENAI_API_BASE', 'https://api.openai.com/v1') #使用api-for-open-llm例子 http://127.0.0.1:8000/v1

# sakura
SAKURA_API_BASE = os.getenv('SAKURA_API_BASE', 'http://127.0.0.1:8080/v1') #SAKURA API地址
SAKURA_VERSION = os.getenv('SAKURA_VERSION', '0.9') #SAKURA API版本，可选值：0.9、0.10，选择0.10则会加载术语表。
SAKURA_DICT_PATH = os.getenv('SAKURA_DICT_PATH', './dict/sakura_dict.txt') #SAKURA 术语表路径


CAIYUN_TOKEN = os.getenv('CAIYUN_TOKEN', '') # 彩云小译API访问令牌

# Gemini
try:
    from .gemini_keys import parse_gemini_keys, parse_gemini_models
except (ImportError, ModuleNotFoundError):
    from gemini_keys import parse_gemini_keys, parse_gemini_models
GEMINI_API_KEYS = parse_gemini_keys([os.getenv('GEMINI_API_KEYS', ''), os.getenv('GEMINI_API_KEY', '')])
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ''

_env_gemini_models = os.getenv('GEMINI_MODELS', '')
_env_gemini_model = os.getenv('GEMINI_MODEL', '')

if _env_gemini_models:
    GEMINI_MODELS = parse_gemini_models(_env_gemini_models)
elif _env_gemini_model:
    GEMINI_MODELS = parse_gemini_models(_env_gemini_model)
else:
    GEMINI_MODELS = ['gemini-1.5-flash-002']

GEMINI_MODEL = GEMINI_MODELS[0] if GEMINI_MODELS else 'gemini-1.5-flash-002'

# deepseek
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', '')
DEEPSEEK_API_BASE  = os.getenv('DEEPSEEK_API_BASE', 'https://api.deepseek.com')
DEEPSEEK_MODEL  = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat') # Or: "deepseek-reasoner"

# Together AI
TOGETHER_API_KEY = os.getenv('TOGETHER_API_KEY', '')
TOGETHER_VL_MODEL = os.getenv('TOGETHER_VL_MODEL', 'Qwen/Qwen2.5-VL-72B-Instruct')

# ollama, with OpenAI API compatibility
CUSTOM_OPENAI_API_KEY = os.getenv('CUSTOM_OPENAI_API_KEY', 'ollama') # Unsed for ollama, but maybe useful for other LLM tools.
CUSTOM_OPENAI_API_BASE = os.getenv('CUSTOM_OPENAI_API_BASE', 'http://localhost:11434/v1') # Use OLLAMA_HOST env to change binding IP and Port.
CUSTOM_OPENAI_MODEL = os.getenv('CUSTOM_OPENAI_MODEL', '') # e.g "qwen2.5:7b". Make sure to pull and run it before use.
CUSTOM_OPENAI_MODEL_CONF = os.getenv('CUSTOM_OPENAI_MODEL_CONF', '') # e.g "qwen2".

# OpenRouter
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
OPENROUTER_API_BASE = os.getenv('OPENROUTER_API_BASE', 'https://openrouter.ai/api/v1')
OPENROUTER_MODEL = os.getenv('OPENROUTER_MODEL', 'deepseek/deepseek-v4-flash-0731')
OPENROUTER_PROVIDER_SORT = os.getenv('OPENROUTER_PROVIDER_SORT', 'price')
