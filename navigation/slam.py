"""Backward-compatibility alias for shared.navigation.slam.

This shim exists so legacy imports continue to work.
New code should import from shared.navigation.slam directly.
"""

from shared.navigation import slam as _impl
import sys as _sys

_sys.modules[__name__] = _impl
