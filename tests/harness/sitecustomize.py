"""Opt-in child-process hook that activates the P0 external-network guard.

The matrix runner prepends this directory to PYTHONPATH only for offline child
test processes. Python imports sitecustomize during startup before pytest
collects plugin modules.
"""

from __future__ import annotations

import os


if os.environ.get("XTW_TEST_NETWORK_DENY") == "1":
    from network_guard import install_global_network_guard

    install_global_network_guard()
