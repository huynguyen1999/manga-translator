import colorama
import threading
from dotenv import load_dotenv
from tqdm import tqdm

colorama.init(autoreset=True)
load_dotenv()

# Pipeline progress bars share threads, not processes; avoid tqdm's semaphore-backed default lock.
tqdm.set_lock(threading.RLock())

from .config import Config
from .utils import Context, get_logger
logger = get_logger('translator')

__all__ = ['Config', 'Context', 'logger', 'MangaTranslator']

def __getattr__(name):
    if name in ('MangaTranslator', 'TranslationInterrupt'):
        from .manga_translator import MangaTranslator, TranslationInterrupt
        if name == 'MangaTranslator':
            return MangaTranslator
        return TranslationInterrupt
    try:
        import manga_translator.manga_translator as mt
        return getattr(mt, name)
    except Exception:
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
