"""Exceptions raised by the translation pipeline."""


class TranslationFailure(RuntimeError):
    """A page has missing dialogue after its translation retries."""
