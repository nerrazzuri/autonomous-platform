"""Backward-compatibility alias for shared.quadruped.state_monitor.

This shim exists so legacy imports continue to work.
New code should import from shared.quadruped.state_monitor directly.
"""

from shared.quadruped import state_monitor as _impl
import sys as _sys

_sys.modules[__name__] = _impl
