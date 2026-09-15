"""glass.adapters — data sources for the components. Each has a fixture path."""
from __future__ import annotations

import sys
from types import ModuleType


def dashboard() -> ModuleType:
    """The RUNNING server module. launch_dashboard.sh runs `python server.py`, so
    the live app is `__main__` — a bare `import server` would build a SECOND app
    (own relay, empty _activity/_last_status) and read stale/empty state.
    Found the hard way: fleet showed "fw ?" while /api/devices knew 0.27.0-u2."""
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "_fleet") and hasattr(main, "_relay"):
        return main
    import server  # uvicorn server:app / tests
    return server
