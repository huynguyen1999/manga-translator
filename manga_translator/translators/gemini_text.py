try:
    import regex as re
except ImportError:
    import re


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
