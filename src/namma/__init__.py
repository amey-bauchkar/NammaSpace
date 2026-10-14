"""Namma Space foundation (Phase 11, Track A).

Synthetic-fixture foundation only: nothing in this package performs or proves
smartphone reconstruction. See docs/DEVELOPMENT.md.
"""
from pathlib import Path

__version__ = "0.11.0"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_VERSION = "1.1.0"          # IF-01..IF-10, IF-12 (additive over 1.0.0; ARCH-026)
RELEASE_CONTRACT_VERSION = "2.0.0"  # IF-11 (ARCH-022)
