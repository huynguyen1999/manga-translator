import asyncio
from argparse import ArgumentParser
from manga_translator.utils import ModelWrapper
from manga_translator.detection import DETECTORS
from manga_translator.ocr import OCRS
from manga_translator.inpainting import INPAINTERS


def parse_args(args=None):
    arg_parser = ArgumentParser()
    arg_parser.add_argument("--models", default="")
    arg_parser.add_argument("--continue-on-error", action="store_true")
    return arg_parser.parse_args(args)


def is_model_selected(family: str, key: str, models: set[str]) -> bool:
    if not models or "all" in models:
        return True
    key_str = str(key).lower()
    candidates = {
        key_str,
        f"{family}.{key_str}",
        key_str.replace("_", "-"),
        f"{family}.{key_str.replace('_', '-')}",
        key_str.replace("-", "_"),
        f"{family}.{key_str.replace('-', '_')}",
    }
    if key_str in ("mocr", "manga_ocr", "mangaocr"):
        candidates.update({"mocr", "ocr.mocr", "manga_ocr", "ocr.manga_ocr", "mangaocr", "ocr.mangaocr"})
    return bool(candidates & models)


async def download(model_dict: dict, continue_on_error: bool = False):
    """Downloads models in the provided dictionary."""
    for key, value in model_dict.items():
        if issubclass(value, ModelWrapper):
            print(" -- Downloading", key)
            try:
                inst = value()
                await inst.download()
            except Exception as e:
                print("Failed to download", key, value)
                print(e)
                if not continue_on_error:
                    raise


async def main(args=None):
    cli_args = parse_args(args)
    models: set[str] = set(filter(None, [m.strip().lower() for m in cli_args.models.split(",")]))

    await download(
        {
            k: v
            for k, v in DETECTORS.items()
            if is_model_selected("detector", str(k), models)
        },
        continue_on_error=cli_args.continue_on_error,
    )
    await download(
        {
            k: v
            for k, v in OCRS.items()
            if is_model_selected("ocr", str(k), models)
        },
        continue_on_error=cli_args.continue_on_error,
    )
    await download(
        {
            k: v
            for k, v in INPAINTERS.items()
            if is_model_selected("inpaint", str(k), models)
            and (str(k).lower() != "sd" or bool({"sd", "inpaint.sd"} & models))
        },
        continue_on_error=cli_args.continue_on_error,
    )


if __name__ == "__main__":
    asyncio.run(main())
