"""The language the E1 goldens run in (docs/design/i18n.md): zh-Hant by default, so an instance on zh-Hant stays
byte-identical to the output before translation; I18N_GOLDEN_LANG=en runs the same cases on en and compares them with
tests/fixtures/i18n-golden/en/ (reviewed English output). tests/test_i18n_golden_en.py runs that second pass.

    I18N_GOLDEN_LANG=en bin/py -m unittest tests.test_i18n_golden_hub_run
"""
import os
import pathlib

LANG = os.environ.get("I18N_GOLDEN_LANG") or "zh-Hant"
BASE = pathlib.Path(__file__).resolve().parent / "fixtures" / "i18n-golden"
GOLDEN = BASE if LANG == "zh-Hant" else BASE / LANG   # where this language's expected outputs are
INPUTS = BASE                                         # case inputs, the same in every language
