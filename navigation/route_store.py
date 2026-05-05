"""Backward-compatibility alias for shared.navigation.route_store.

This shim exists so legacy imports continue to work.
New code should import from shared.navigation.route_store directly.
"""

from shared.navigation import route_store as _impl
import sys as _sys

_sys.modules[__name__] = _impl
