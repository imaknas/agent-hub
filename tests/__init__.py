"""Run as a package (`bin/py -m unittest tests.<module>`, `discover -s tests -t .`), the tests find their helpers
(golden_lang, store_fixture…) as bin/run-tests's PYTHONPATH gives them, and a run started by hand is guarded before any
test module loads: hubconf's import runs it again under bin/testguard.py's throwaway platform (a module that never
imports hubconf still gets its own temp folder that way)."""
import pathlib
import sys

_here = pathlib.Path(__file__).resolve().parent
for _p in (_here.parent / "bin", _here):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import hubconf  # noqa: E402,F401
