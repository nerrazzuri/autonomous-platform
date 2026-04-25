from apps.logistics.runtime import startup as _impl
import sys as _sys

if __name__ == "__main__":
    _impl.main()
else:
    _sys.modules[__name__] = _impl
