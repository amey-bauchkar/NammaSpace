"""Start the Namma Space Studio (local web app).

    python scripts/start_studio.py [--port 8780] [--projects projects] [--no-lan]

Equivalent to `python -m namma.studio.server` with src/ on the Python path. Open http://127.0.0.1:8780/.
LAN phone auto-capture over HTTPS is enabled by default as Standard Operating Procedure (SOP).
"""
import sys

from _bootstrap import ROOT  # noqa: F401  (adds src/ to sys.path)

from namma.studio.server import main

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
