"""Backward-compatibility alias for shared.quadruped.heartbeat.

This shim exists so legacy imports continue to work.
New code should import from shared.quadruped.heartbeat directly.
"""

from shared.quadruped import heartbeat as _impl
import sys as _sys

_sys.modules[__name__] = _impl
