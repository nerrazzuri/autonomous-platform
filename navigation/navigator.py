"""Backward-compatibility alias for shared.navigation.navigator.

This shim exists so legacy imports continue to work.
New code should import from shared.navigation.navigator directly.
"""

from shared.navigation import navigator as _impl
import sys as _sys

_sys.modules[__name__] = _impl
