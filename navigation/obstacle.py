"""Backward-compatibility alias for shared.navigation.obstacle.

This shim exists so legacy imports continue to work.
New code should import from shared.navigation.obstacle directly.
"""

from shared.navigation import obstacle as _impl
import sys as _sys

_sys.modules[__name__] = _impl
