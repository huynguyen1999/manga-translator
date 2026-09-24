import json

try:
    import deepl
except ImportError:
    deepl = None

from .common import CommonTranslator, MissingAPIKeyException
from .keys import DEEPL_AUTH_KEY

class DeeplTranslator(CommonTranslator):
    _LANGUAGE_CODE_MAP = {
        'CHS': 'ZH-HANS',
        'CHT': 'ZH-HANT',
        'JPN': 'JA',
        'ENG': 'EN-US',
        'CSY': 'CS',
        'NLD': 'NL',
        'FRA': 'FR',
        'DEU': 'DE',
        'HUN': 'HU',
        'ITA': 'IT',
        'POL': 'PL',
        'PTB': 'PT-BR',
        'ROM': 'RO',
        'RUS': 'RU',
        'ESP': 'ES',
        'IND': 'ID',
        'ARA': 'AR',
        'BGR': 'BG',
        'BUL': 'BG',
        'DAN': 'DA',
        'ELL': 'EL',
        'EST': 'ET',
        'FIN': 'FI',
        'KOR': 'KO',
        'LTH': 'LT',
        'LIT': 'LT',
        'LAV': 'LV',
        'NOB': 'NB',
        'SVK': 'SK',
        'SLO': 'SK',
        'SLV': 'SL',
        'SWE': 'SV',
        'TRK': 'TR',
        'TUR': 'TR',
        'UKR': 'UK'
    }

    def __init__(self):
        super().__init__()
        if deepl is None:
            raise ImportError('Please install the deepl package to use the deepl translator (pip install deepl).')
        if not DEEPL_AUTH_KEY:
            raise MissingAPIKeyException('Please set the DEEPL_AUTH_KEY environment variable before using the deepl translator.')
        self.translator = deepl.Translator(DEEPL_AUTH_KEY)

    async def _translate(self, from_lang, to_lang, queries):
        if not queries:
            return []

        # DeepL limits each text request body to 128 KiB.
        max_request_bytes = 128 * 1024
        base_size = len(json.dumps({
            'target_lang': to_lang, 'text': [], 'show_billed_characters': True,
        }).encode('utf-8'))
        batch_size = base_size
        batches = []
        batch = []
        for query in queries:
            query_size = len(json.dumps(query).encode('utf-8'))
            if batch and batch_size + query_size + 1 > max_request_bytes:
                batches.append(batch)
                batch = []
                batch_size = base_size
            batch_size += query_size + (1 if batch else 0)
            batch.append(query)
        if batch:
            batches.append(batch)

        return [
            result.text
            for batch in batches
            for result in self.translator.translate_text(batch, target_lang=to_lang)
        ]
