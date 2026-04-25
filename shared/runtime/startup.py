from __future__ import annotations

"""Compatibility startup shim for shared runtime imports."""

from shared.runtime.base_startup import shutdown_system, startup_system
from apps.logistics.runtime.startup import create_uvicorn_config, main


__all__ = ["create_uvicorn_config", "main", "shutdown_system", "startup_system"]


if __name__ == "__main__":
    main()
