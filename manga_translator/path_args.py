"""Shared filesystem argument converters for the translator and server CLIs."""

import argparse
import os
from urllib.parse import unquote


def url_decode(value: str) -> str:
    value = unquote(value)
    if value.startswith("file:///"):
        value = value[len("file://"):]
    return value


def _existing_path(value: str, error: str) -> str:
    if not value:
        return ""
    path = url_decode(os.path.expanduser(value))
    if not os.path.exists(path):
        raise argparse.ArgumentTypeError(f'{error}: "{value}"')
    return path


def path(value: str) -> str:
    return _existing_path(value, "No such file or directory")


def file_path(value: str) -> str:
    return _existing_path(value, "No such file")


def dir_path(value: str) -> str:
    return _existing_path(value, "No such directory")
