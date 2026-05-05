"""Backward-compatibility alias for shared.quadruped.sdk_adapter.

This shim exists so legacy imports continue to work.
New code should import from shared.quadruped.sdk_adapter directly.
"""

from shared.quadruped import sdk_adapter as _impl
import sys as _sys

_sys.modules[__name__] = _impl
