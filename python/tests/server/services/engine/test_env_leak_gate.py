"""Tests for EnvLeakGate — Pre-spawn security scanner."""

import os
import tempfile

import pytest

from src.server.services.engine.env_leak_gate import (
    EnvLeakError,
    EnvLeakGate,
    EnvScanResult,
    get_env_leak_gate,
)


@pytest.fixture
def gate() -> EnvLeakGate:
    return EnvLeakGate()


@pytest.fixture
def project_dir():
    """Create a temporary project directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestScanCleanProject:
    """Test scanning a project with no .env files or no sensitive keys."""

    def test_no_env_files(self, gate: EnvLeakGate, project_dir: str) -> None:
        result = gate.scan(project_dir)
        assert result.blocked is False
        assert result.keys_found == []
        assert result.files_scanned == []

    def test_empty_env_file(self, gate: EnvLeakGate, project_dir: str) -> None:
        env_file = os.path.join(project_dir, ".env")
        with open(env_file, "w") as f:
            f.write("")
        result = gate.scan(project_dir)
        assert result.blocked is False
        assert result.keys_found == []

    def test_safe_env_file(self, gate: EnvLeakGate, project_dir: str) -> None:
        env_file = os.path.join(project_dir, ".env")
        with open(env_file, "w") as f:
            f.write("APP_NAME=myapp\nPORT=3000\nDEBUG=true\n")
        result = gate.scan(project_dir)
        assert result.blocked is False
        assert result.keys_found == []


class TestScanSensitiveKeys:
    """Test detection of sensitive keys."""

    def test_detects_supabase_service_key(self, gate: EnvLeakGate, project_dir: str) -> None:
        env_file = os.path.join(project_dir, ".env")
        with open(env_file, "w") as f:
            f.write("SUPABASE_URL=https://example.supabase.co\nSUPABASE_SERVICE_KEY=eyJhbGciOi...\n")
        result = gate.scan(project_dir)
        assert result.blocked is True
        assert "SUPABASE_SERVICE_KEY" in result.keys_found

    def test_detects_anthropic_api_key(self, gate: EnvLeakGate, project_dir: str) -> None:
        env_file = os.path.join(project_dir, ".env")
        with open(env_file, "w") as f:
            f.write("ANTHROPIC_API_KEY=sk-ant-api03-...\n")
        result = gate.scan(project_dir)
        assert result.blocked is True
        assert "ANTHROPIC_API_KEY" in result.keys_found

    def test_detects_multiple_keys(self, gate: EnvLeakGate, project_dir: str) -> None:
        env_file = os.path.join(project_dir, ".env")
        with open(env_file, "w") as f:
            f.write("OPENAI_API_KEY=sk-...\nANTHROPIC_API_KEY=sk-ant-...\nDATABASE_URL=postgres://...\n")
        result = gate.scan(project_dir)
        assert result.blocked is True
        assert len(result.keys_found) == 3

    def test_detects_keys_in_env_local(self, gate: EnvLeakGate, project_dir: str) -> None:
        env_file = os.path.join(project_dir, ".env.local")
        with open(env_file, "w") as f:
            f.write("SECRET_KEY=my-very-secret-key\n")
        result = gate.scan(project_dir)
        assert result.blocked is True
        assert "SECRET_KEY" in result.keys_found

    def test_detects_keys_in_python_subdir(self, gate: EnvLeakGate, project_dir: str) -> None:
        python_dir = os.path.join(project_dir, "python")
        os.makedirs(python_dir, exist_ok=True)
        env_file = os.path.join(python_dir, ".env")
        with open(env_file, "w") as f:
            f.write("JWT_SECRET=some-jwt-secret\n")
        result = gate.scan(project_dir)
        assert result.blocked is True
        assert "JWT_SECRET" in result.keys_found


class TestConsentBypass:
    """Test that allow_keys=True bypasses the block."""

    def test_consent_allows_keys(self, gate: EnvLeakGate, project_dir: str) -> None:
        env_file = os.path.join(project_dir, ".env")
        with open(env_file, "w") as f:
            f.write("ANTHROPIC_API_KEY=sk-ant-...\n")
        result = gate.scan(project_dir, allow_keys=True)
        assert result.blocked is False
        assert "ANTHROPIC_API_KEY" in result.keys_found  # Still found, just not blocked


class TestCheckOrRaise:
    """Test the convenience method that raises on block."""

    def test_raises_env_leak_error(self, gate: EnvLeakGate, project_dir: str) -> None:
        env_file = os.path.join(project_dir, ".env")
        with open(env_file, "w") as f:
            f.write("OPENAI_API_KEY=sk-...\n")
        with pytest.raises(EnvLeakError) as exc_info:
            gate.check_or_raise(project_dir, task_id="test-task-123")
        assert exc_info.value.scan_result is not None
        assert exc_info.value.scan_result.blocked is True

    def test_does_not_raise_when_clean(self, gate: EnvLeakGate, project_dir: str) -> None:
        env_file = os.path.join(project_dir, ".env")
        with open(env_file, "w") as f:
            f.write("APP_NAME=myapp\n")
        result = gate.check_or_raise(project_dir, task_id="test-task-123")
        assert result.blocked is False

    def test_does_not_raise_with_consent(self, gate: EnvLeakGate, project_dir: str) -> None:
        env_file = os.path.join(project_dir, ".env")
        with open(env_file, "w") as f:
            f.write("ANTHROPIC_API_KEY=sk-ant-...\n")
        result = gate.check_or_raise(project_dir, allow_keys=True, task_id="test-task-123")
        assert result.blocked is False


class TestScanResultSummary:
    """Test EnvScanResult summary property."""

    def test_clean_summary(self) -> None:
        result = EnvScanResult(files_scanned=[".env", ".env.local"])
        assert "Clean" in result.summary

    def test_blocked_summary(self) -> None:
        result = EnvScanResult(
            blocked=True,
            keys_found=["ANTHROPIC_API_KEY", "OPENAI_API_KEY"],
            files_with_keys=[".env"],
        )
        assert "BLOCKED" in result.summary
        assert "2 sensitive key(s)" in result.summary


class TestCustomPatterns:
    """Test custom pattern extension."""

    def test_extra_key_pattern(self, project_dir: str) -> None:
        gate = EnvLeakGate(extra_key_patterns=["MY_CUSTOM_SECRET"])
        env_file = os.path.join(project_dir, ".env")
        with open(env_file, "w") as f:
            f.write("MY_CUSTOM_SECRET=supersecret\n")
        result = gate.scan(project_dir)
        assert result.blocked is True
        assert "MY_CUSTOM_SECRET" in result.keys_found


class TestSingleton:
    """Test module-level singleton."""

    def test_singleton_returns_same_instance(self) -> None:
        g1 = get_env_leak_gate()
        g2 = get_env_leak_gate()
        assert g1 is g2
