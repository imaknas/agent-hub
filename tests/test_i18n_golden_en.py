"""E1 of docs/design/i18n.md, second pass: every golden case again on en, compared with tests/fixtures/i18n-golden/en/.

The zh-Hant pass (the test_i18n_golden* modules themselves) keeps this instance byte-identical; this pass pins the
English an en instance sees, reviewed once and then changed only on purpose. It runs the same modules in a subprocess
with I18N_GOLDEN_LANG=en (tests/golden_lang.py). The subprocess starts when this module is imported, so under
`unittest discover` it runs while the rest of the suite does instead of after it; the test waits for it.

    bin/py -m unittest tests.test_i18n_golden_en
    I18N_GOLDEN_LANG=en I18N_GOLDEN_UPDATE=1 bin/py -m unittest tests.test_i18n_golden_hub_run   # rewrite one (review it)
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
MODULES = sorted(p.stem for p in TESTS.glob("test_i18n_golden*.py") if p.stem != pathlib.Path(__file__).stem)
_proc = None
if not os.environ.get("I18N_GOLDEN_LANG"):   # not inside the en pass itself
    _out = tempfile.TemporaryFile(mode="w+")
    _proc = subprocess.Popen([sys.executable, "-m", "unittest", *MODULES], cwd=TESTS, stdout=_out,
                             stderr=subprocess.STDOUT, text=True,
                             env={**os.environ, "I18N_GOLDEN_LANG": "en", "PYTHONDONTWRITEBYTECODE": "1"})


class English(unittest.TestCase):
    @unittest.skipIf(_proc is None, "this is the en pass itself")
    def test_goldens_on_en(self):
        code = _proc.wait(timeout=900)
        _out.seek(0)
        log = _out.read()
        self.assertEqual(code, 0, "the en pass of the goldens failed:\n" + log[-6000:])


if __name__ == "__main__":
    unittest.main()
