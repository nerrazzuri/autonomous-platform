from __future__ import annotations

"""Deprecated compatibility shim for logistics startup.

This module is retained so legacy imports of `shared.runtime.startup` continue to work.
New code should use `main.py --app logistics` or `apps.logistics.runtime.startup`
directly.
"""

from shared.runtime.base_startup import shutdown_system, startup_system
from apps.logistics.runtime.startup import create_uvicorn_config, main


__all__ = ["create_uvicorn_config", "main", "shutdown_system", "startup_system"]


if __name__ == "__main__":
    main()
