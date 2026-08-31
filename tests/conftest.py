"""Test-only import setup for the standalone shadow orchestrator package."""

from __future__ import annotations

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODIFIED_PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "modified"
if str(MODIFIED_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(MODIFIED_PLUGIN_ROOT))
