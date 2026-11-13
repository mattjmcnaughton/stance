"""Shared test setup.

Importing ``stance`` rewrites ``os.environ`` (privacy opt-outs and the
Stance-owned ``OMNIGENT_DATA_DIR``), so point the XDG directories at a
throwaway location before any test module imports it.
"""

import os
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="stance-tests-")
os.environ["XDG_STATE_HOME"] = os.path.join(_SANDBOX, "state")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SANDBOX, "config")

# Apply Stance's bootstrap before any test helper imports Omnigent.
import stance  # noqa: E402, F401
