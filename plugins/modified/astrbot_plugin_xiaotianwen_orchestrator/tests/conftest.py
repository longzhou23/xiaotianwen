"""Make the standalone plugin package importable without AstrBot installed."""

from __future__ import annotations

import sys
from pathlib import Path


MODIFIED_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(MODIFIED_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(MODIFIED_PLUGIN_ROOT))
