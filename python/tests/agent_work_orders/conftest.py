"""Pytest configuration for agent_work_orders tests"""

import importlib
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Set ENABLE_AGENT_WORK_ORDERS=true for all tests so health endpoint populates dependencies
os.environ.setdefault("ENABLE_AGENT_WORK_ORDERS", "true")

# Mock get_supabase_client before any modules import it
# This prevents Supabase credential validation during test collection
mock_client = MagicMock()
try:
    importlib.import_module("src.agent_work_orders.state_manager.repository_config_repository")
except Exception:
    parent_name = "src.agent_work_orders.state_manager"
    module_name = f"{parent_name}.repository_config_repository"
    parent_module = sys.modules.get(parent_name)
    if parent_module is None:
        parent_module = types.ModuleType(parent_name)
        sys.modules[parent_name] = parent_module
    repo_module = types.ModuleType(module_name)
    repo_module.get_supabase_client = MagicMock(return_value=mock_client)
    sys.modules[module_name] = repo_module
    setattr(parent_module, "repository_config_repository", repo_module)

try:
    importlib.import_module("src.agent_work_orders.server")
except Exception:
    pass

mock_get_client = patch(
    "src.agent_work_orders.state_manager.repository_config_repository.get_supabase_client",
    return_value=mock_client
)
mock_get_client.start()


@pytest.fixture(autouse=True)
def reset_structlog():
    """Reset structlog configuration for each test"""
    try:
        import structlog
    except ModuleNotFoundError:
        yield
        return

    structlog.reset_defaults()
    yield
