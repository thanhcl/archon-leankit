"""
Env-Leak Gate — Pre-spawn security scanner for sensitive keys.

Adopted from upstream Archon v0.3.2 env-leak-gate pattern.
Scans .env files in the project directory for sensitive keys
before spawning agent processes. Fail-closed by default.

Usage:
    gate = EnvLeakGate()
    result = gate.scan(project_path="/path/to/repo")
    if result.blocked:
        # Do not spawn — sensitive keys found
        logger.error(f"Env-leak gate blocked spawn: {result.keys_found}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Sensitive key patterns — keys whose presence in .env files
# indicates risk of leaking credentials to spawned agent processes.
SENSITIVE_KEY_PATTERNS: list[str] = [
    "SUPABASE_SERVICE_KEY",
    "SUPABASE_ANON_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GITHUB_TOKEN",
    "GITHUB_PAT",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "STRIPE_SECRET_KEY",
    "DATABASE_URL",
    "DATABASE_PASSWORD",
    "DB_PASSWORD",
    "PRIVATE_KEY",
    "SECRET_KEY",
    "JWT_SECRET",
    "ENCRYPTION_KEY",
]

# Compiled regex to match KEY=VALUE lines in .env files
_KEY_PATTERN = re.compile(
    r"^\s*(" + "|".join(re.escape(k) for k in SENSITIVE_KEY_PATTERNS) + r")\s*=",
    re.MULTILINE | re.IGNORECASE,
)

# Files to scan (relative to project root)
ENV_FILE_NAMES: list[str] = [
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "python/.env",
]


@dataclass
class EnvScanResult:
    """Result of an environment file scan."""

    blocked: bool = False
    keys_found: list[str] = field(default_factory=list)
    files_scanned: list[str] = field(default_factory=list)
    files_with_keys: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.blocked:
            return f"Clean — scanned {len(self.files_scanned)} files, no sensitive keys found"
        return (
            f"BLOCKED — found {len(self.keys_found)} sensitive key(s) in "
            f"{len(self.files_with_keys)} file(s): {', '.join(self.keys_found)}"
        )


class EnvLeakGate:
    """Pre-spawn security gate that scans for sensitive environment keys.

    Fail-closed: if scanning fails for any reason, the gate blocks.
    Consent-aware: projects can opt-in to bypass the gate.
    """

    def __init__(
        self,
        extra_key_patterns: list[str] | None = None,
        extra_file_names: list[str] | None = None,
    ) -> None:
        self._key_patterns = SENSITIVE_KEY_PATTERNS + (extra_key_patterns or [])
        self._file_names = ENV_FILE_NAMES + (extra_file_names or [])
        self._key_pattern = re.compile(
            r"^\s*(" + "|".join(re.escape(k) for k in self._key_patterns) + r")\s*=",
            re.MULTILINE | re.IGNORECASE,
        )

    def scan(
        self,
        project_path: str,
        allow_keys: bool = False,
    ) -> EnvScanResult:
        """Scan project .env files for sensitive keys.

        Args:
            project_path: Root directory of the project.
            allow_keys: If True, scan but don't block (consent granted).

        Returns:
            EnvScanResult with blocked status and found keys.
        """
        root = Path(project_path)
        result = EnvScanResult()

        for fname in self._file_names:
            env_file = root / fname
            if not env_file.is_file():
                continue

            result.files_scanned.append(str(env_file))

            try:
                content = env_file.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                logger.warning(f"Env-leak gate: cannot read {env_file}: {e}")
                # Fail-closed: treat unreadable files as potentially dangerous
                result.blocked = True
                result.files_with_keys.append(str(env_file))
                result.keys_found.append(f"<unreadable:{fname}>")
                continue

            matches = self._key_pattern.findall(content)
            if matches:
                unique_keys = sorted(set(matches))
                result.keys_found.extend(unique_keys)
                result.files_with_keys.append(str(env_file))

        # Deduplicate keys
        result.keys_found = sorted(set(result.keys_found))

        # Block unless consent granted
        if result.keys_found and not allow_keys:
            result.blocked = True

        # Audit log
        if result.blocked:
            logger.warning(
                f"Env-leak gate BLOCKED | project={project_path} | "
                f"keys={result.keys_found} | files={result.files_with_keys}"
            )
        elif result.keys_found:
            logger.info(
                f"Env-leak gate ALLOWED (consent) | project={project_path} | "
                f"keys={result.keys_found}"
            )
        else:
            logger.debug(
                f"Env-leak gate CLEAN | project={project_path} | "
                f"scanned={len(result.files_scanned)} files"
            )

        return result

    def check_or_raise(
        self,
        project_path: str,
        allow_keys: bool = False,
        task_id: str | None = None,
    ) -> EnvScanResult:
        """Scan and raise EnvLeakError if blocked.

        Convenience method for use in the task execution pipeline.
        """
        result = self.scan(project_path, allow_keys=allow_keys)
        if result.blocked:
            raise EnvLeakError(
                f"Env-leak gate blocked spawn for task={task_id}: {result.summary}",
                scan_result=result,
            )
        return result


class EnvLeakError(Exception):
    """Raised when env-leak gate detects sensitive keys and consent is not granted."""

    def __init__(self, message: str, scan_result: EnvScanResult | None = None) -> None:
        super().__init__(message)
        self.scan_result = scan_result


# Module-level singleton
_default_gate: EnvLeakGate | None = None


def get_env_leak_gate() -> EnvLeakGate:
    """Get or create the module-level EnvLeakGate singleton."""
    global _default_gate
    if _default_gate is None:
        _default_gate = EnvLeakGate()
    return _default_gate
