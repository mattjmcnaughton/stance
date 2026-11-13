"""Configurable agent sessions and policies, powered by Omnigent."""

import os
from importlib.metadata import version

from stance.privacy import apply_privacy_env

# Runs before any Stance module can import Omnigent: Omnigent caches telemetry
# state and computes data-dir paths at import time.
apply_privacy_env(os.environ)

__version__ = version("stance")
