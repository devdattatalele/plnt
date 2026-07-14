"""plnt runtime — one Protocol, four backends.

`RuntimeAdapter` abstracts over vLLM / TGI / TRT-LLM / SGLang. Concrete
implementations live in `plnt/playground/backends.py` (the API gateway uses
them) and would grow into per-runtime pods in future phases.
"""

from plnt.runtime.base import RuntimeAdapter

__all__ = ["RuntimeAdapter"]
