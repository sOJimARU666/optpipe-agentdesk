"""Installs the runtime network guard in every Python subprocess the tests start (optpipe CLI, scripts/*.py).
The conftest puts this folder on PYTHONPATH and sets OPTPIPE_NETGUARD=1. Any other sitecustomize on the path is
then loaded as usual, so the system one is not lost."""
import importlib.util as _u
import os as _os
import sys as _sys

_here = _os.path.dirname(_os.path.abspath(__file__))
if _os.environ.get("OPTPIPE_NETGUARD") == "1":
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    import optpipe_netguard as _g
    _g.install()

for _p in _sys.path:
    if _p and _os.path.abspath(_p) != _here:
        _f = _os.path.join(_p, "sitecustomize.py")
        if _os.path.isfile(_f):
            _spec = _u.spec_from_file_location("_next_sitecustomize", _f)
            try:
                _spec.loader.exec_module(_u.module_from_spec(_spec))
            except Exception:
                pass
            break
