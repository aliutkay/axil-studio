"""Interface languages.

The source language of AXIL Studio is English: every user-visible text in the code is written in
English and passed through tr(). A language is a JSON catalog next to this file (<code>.json,
{"English text": "translation"}); texts missing from it stay English. Texts with values use
numbered placeholders, filled after the translation: tr("{0} registers").format(n).

The language is chosen in View → Language (stored in the settings file below) or with the
environment variable AXIL_STUDIO_LANG (e.g. AXIL_STUDIO_LANG=tr). It is read once, so a change
takes effect after a restart. This module does not use Qt: the core (generators, command line)
uses it too.

Adding a language: copy tr.json to <code>.json, translate the values, add the code to LANGUAGES.
scripts/i18n_check.py lists the texts of the code missing from a catalog.
"""

import json
import os
import sys

LANGUAGES = {"en": "English", "tr": "Türkçe"}
DEFAULT = "en"
ENV = "AXIL_STUDIO_LANG"

_lang = None
_catalog = {}


def settings_path():
    """Small settings file shared by the application and the command line."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "AXIL Studio", "settings.json")


def _read_settings():
    try:
        with open(settings_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def saved_language():
    """The language chosen in the settings (not the environment override)."""
    code = _read_settings().get("language", DEFAULT)
    return code if code in LANGUAGES else DEFAULT


def set_saved_language(code):
    """Store the language for the next start."""
    if code not in LANGUAGES:
        raise ValueError(code)
    data = _read_settings()
    data["language"] = code
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def catalog_path(code):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{code}.json")


def load_catalog(code):
    if code == "en":
        return {}
    try:
        with open(catalog_path(code), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def language():
    """The interface language of this run."""
    global _lang, _catalog
    if _lang is None:
        code = os.environ.get(ENV) or saved_language()
        _lang = code if code in LANGUAGES else DEFAULT
        _catalog = load_catalog(_lang)
    return _lang


def use(code):
    """Switch the language of this process (tests, command line); texts made before keep theirs."""
    global _lang, _catalog
    _lang = code if code in LANGUAGES else DEFAULT
    _catalog = load_catalog(_lang)


def tr(text):
    """`text` (English) in the interface language."""
    if _lang is None:
        language()
    return _catalog.get(text, text) if _catalog else text
