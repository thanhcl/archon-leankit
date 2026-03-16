"""Local conftest that pre-patches modules before root conftest runs."""

import sys
from unittest.mock import MagicMock

# Pre-populate sys.modules so root conftest's patch targets resolve
for mod_path in [
    "src.server.utils",
    "src.server.services.client_manager",
]:
    if mod_path not in sys.modules:
        sys.modules[mod_path] = MagicMock()
