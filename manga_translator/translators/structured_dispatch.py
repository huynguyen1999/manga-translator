"""ID-preserving translation dispatch, separated from translator registration."""

from typing import Optional

from ..config import Translator, TranslatorChain, TranslatorConfig
from ..utils import Context


async def dispatch_structured(
    chain: TranslatorChain,
    items: list[tuple[str, str]],
    translator_config: Optional[TranslatorConfig] = None,
    args: Optional[Context] = None,
    device: str = "cpu",
) -> dict[str, str]:
    from . import (
        GPT_TRANSLATORS,
        OFFLINE_TRANSLATORS,
        OfflineTranslator,
        _run_offline_operation,
        _translate_with_context,
        _translation_resource_lease,
        _wait_for_gpt_translation,
        get_translator,
        translate_structured,
    )

    values = dict(items)
    for key, target_lang in chain.chain:
        translator = get_translator(key)
        lease_args = None if key in {Translator.none, Translator.original} else args

        async def translate_one():
            nonlocal values
            async with _translation_resource_lease(lease_args, key in OFFLINE_TRANSLATORS, device):
                if key in OFFLINE_TRANSLATORS and isinstance(translator, OfflineTranslator):
                    await translator.load("auto", target_lang, device)
                if translator_config:
                    translator.parse_args(translator_config)
                if key in GPT_TRANSLATORS:
                    values = await _wait_for_gpt_translation(
                        translate_structured(translator, target_lang, values.items())
                    )
                else:
                    translated = await _translate_with_context(
                        key, translator, "auto", target_lang, list(values.values()), False, args
                    )
                    if len(translated) != len(values):
                        raise ValueError("Translator returned an incorrect number of results")
                    values = dict(zip(values, translated))
                model = getattr(translator, "model_name", None) or getattr(translator, "model", None) or getattr(translator, "MODEL", None)
                if args is not None and isinstance(model, str):
                    args["translator_model"] = model

        if key in OFFLINE_TRANSLATORS and args is not None and args.get("_batch_resource_acquire"):
            await _run_offline_operation(translate_one)
        else:
            await translate_one()
    return values
