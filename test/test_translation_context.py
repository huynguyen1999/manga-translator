from manga_translator.manga_translator import MangaTranslator


def test_context_builder_uses_recent_nonempty_pages_and_original_text():
    translator = MangaTranslator.__new__(MangaTranslator)
    translator.context_size = 2
    translator.all_page_translations = [
        {'id': 'first'},
        {'id': '   '},
        {'id': ' second '},
        {'id': 'third'},
    ]
    translator._original_page_texts = [
        {'id': 'source first'},
        {'id': '   '},
        {'id': 'source second'},
        {'id': 'source third'},
    ]

    assert translator._build_prev_context() == (
        'Here are the previous translation results for reference:\n'
        '<|1|>second\n<|2|>third'
    )
    assert translator._build_prev_context(use_original_text=True) == (
        'Here are the previous original text for reference:\n'
        '<|1|>source second\n<|2|>source third'
    )


def test_batch_context_includes_only_prior_batch_pages_and_resolves_originals():
    translator = MangaTranslator.__new__(MangaTranslator)
    translator.context_size = 1
    translator.all_page_translations = [{'id': 'completed translation'}]
    translator._original_page_texts = [
        {'id': 'completed source'},
        {'id': 'batch source'},
    ]
    batch_texts = [{'id': 'batch translation'}]

    assert translator._build_prev_context(
        batch_index=1,
        batch_original_texts=batch_texts,
    ) == 'Here are the previous translation results for reference:\n<|1|>completed translation'
    assert translator._build_prev_context(
        use_original_text=True,
        batch_index=1,
        batch_original_texts=batch_texts,
    ) == 'Here are the previous original text for reference:\n<|1|>batch source'
