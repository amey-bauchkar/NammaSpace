"""Start Namma Space Studio with LAN phone capture enabled.

Usage:
    python scripts/run_lan.py [--port 8780] [--projects projects]
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "scripts"))

from namma.studio.server import main

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = ["--lan"] + [arg for arg in sys.argv[1:] if arg != "--lan"]
    sys.exit(main(argv))
