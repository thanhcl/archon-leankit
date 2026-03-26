"""Tests for startup configuration validation.

Verifies that required vars cause immediate failure and optional vars emit
warnings as specified by ADR-0008 fail-fast rules.
"""

import logging
import os
from unittest.mock import patch

import pytest


class TestWarnOptionalVars:
    """Tests for warn_optional_vars() in server config."""

    def test_warns_when_openai_api_key_missing(self, caplog: pytest.LogCaptureFixture) -> None:
        """Missing OPENAI_API_KEY emits a warning at startup."""
        from src.server.config.config import warn_optional_vars

        env_override = {"OPENAI_API_KEY": "", "LOGFIRE_TOKEN": "tok"}
        with patch.dict(os.environ, env_override):
            with caplog.at_level(logging.WARNING, logger="src.server.config.config"):
                warn_optional_vars()

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("OPENAI_API_KEY" in msg for msg in warning_messages)

    def test_warns_when_logfire_token_missing(self, caplog: pytest.LogCaptureFixture) -> None:
        """Missing LOGFIRE_TOKEN emits a warning at startup."""
        from src.server.config.config import warn_optional_vars

        env_override = {"OPENAI_API_KEY": "sk-test", "LOGFIRE_TOKEN": ""}
        with patch.dict(os.environ, env_override):
            with caplog.at_level(logging.WARNING, logger="src.server.config.config"):
                warn_optional_vars()

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("LOGFIRE_TOKEN" in msg for msg in warning_messages)

    def test_no_warnings_when_all_optional_vars_set(self, caplog: pytest.LogCaptureFixture) -> None:
        """No warnings emitted when all optional vars are present."""
        from src.server.config.config import warn_optional_vars

        env_override = {"OPENAI_API_KEY": "sk-test", "LOGFIRE_TOKEN": "tok-test"}
        with patch.dict(os.environ, env_override):
            with caplog.at_level(logging.WARNING, logger="src.server.config.config"):
                warn_optional_vars()

        assert not any(r.levelno == logging.WARNING for r in caplog.records)


class TestRequiredVarValidation:
    """Tests for fail-fast validation of required vars in load_environment_config."""

    def test_missing_supabase_url_raises(self) -> None:
        """Missing SUPABASE_URL causes immediate ConfigurationError."""
        from src.server.config.config import ConfigurationError, load_environment_config

        env = {
            "SUPABASE_URL": "",
            "SUPABASE_SERVICE_KEY": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.test",
            "LEANKIT_CONTROL_PLANE_MCP_PORT": "8051",
            "OPENAI_API_KEY": "",
        }
        with patch.dict(os.environ, env):
            with pytest.raises(ConfigurationError, match=r"SUPABASE_URL.*REQUIRED_CONFIG\.md#archon-backend"):
                load_environment_config()

    def test_missing_supabase_service_key_raises(self) -> None:
        """Missing SUPABASE_SERVICE_KEY causes immediate ConfigurationError."""
        from src.server.config.config import ConfigurationError, load_environment_config

        env = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_KEY": "",
            "LEANKIT_CONTROL_PLANE_MCP_PORT": "8051",
            "OPENAI_API_KEY": "",
        }
        with patch.dict(os.environ, env):
            with pytest.raises(ConfigurationError, match=r"SUPABASE_SERVICE_KEY.*REQUIRED_CONFIG\.md#archon-backend"):
                load_environment_config()

    def test_missing_port_raises(self) -> None:
        """Missing port env var causes ConfigurationError."""
        from src.server.config.config import ConfigurationError, load_environment_config

        service_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.test"
        env = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_KEY": service_key,
            "PORT": "",
            "ARCHON_MCP_PORT": "",
            "LEANKIT_CONTROL_PLANE_MCP_PORT": "",
            "OPENAI_API_KEY": "",
        }
        with patch.dict(os.environ, env):
            with pytest.raises(
                ConfigurationError,
                match=r"LEANKIT_CONTROL_PLANE_MCP_PORT, ARCHON_MCP_PORT, or PORT.*REQUIRED_CONFIG\.md#archon-backend",
            ):
                load_environment_config()

    def test_missing_backend_entrypoint_port_raises(self) -> None:
        """Direct backend startup requires a documented bind port variable."""
        from src.server.config.startup_validation import validate_backend_entrypoint_port

        env = {
            "LEANKIT_CONTROL_PLANE_PORT": "",
            "ARCHON_SERVER_PORT": "",
        }
        with pytest.raises(
            ValueError,
            match=r"LEANKIT_CONTROL_PLANE_PORT or ARCHON_SERVER_PORT.*REQUIRED_CONFIG\.md#archon-backend",
        ):
            validate_backend_entrypoint_port(env)

    def test_missing_agents_control_plane_port_raises(self) -> None:
        """Agents startup fails fast without control-plane port configuration."""
        from src.server.config.startup_validation import validate_agents_control_plane_port

        env = {
            "LEANKIT_CONTROL_PLANE_PORT": "",
            "ARCHON_SERVER_PORT": "",
        }
        with pytest.raises(
            ValueError,
            match=r"LEANKIT_CONTROL_PLANE_PORT or ARCHON_SERVER_PORT.*REQUIRED_CONFIG\.md#archon-agents",
        ):
            validate_agents_control_plane_port(env)

    def test_missing_agents_entrypoint_port_raises(self) -> None:
        """Direct agents startup requires a documented bind port variable."""
        from src.server.config.startup_validation import validate_agents_entrypoint_port

        env = {
            "LEANKIT_AGENT_SERVICE_PORT": "",
            "ARCHON_AGENTS_PORT": "",
        }
        with pytest.raises(
            ValueError,
            match=r"LEANKIT_AGENT_SERVICE_PORT or ARCHON_AGENTS_PORT.*REQUIRED_CONFIG\.md#archon-agents",
        ):
            validate_agents_entrypoint_port(env)
