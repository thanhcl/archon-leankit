#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# ///

"""Pre-tool-use hook — security guards + logging, fail-safe wrapper."""

import json
import sys
import re

# Allowed directories where rm -rf is permitted
ALLOWED_RM_DIRECTORIES = [
    'trees/',
]


def is_path_in_allowed_directory(command, allowed_dirs):
    """
    Check if the rm command targets paths exclusively within allowed directories.
    Returns True if all paths in the command are within allowed directories.
    """
    path_pattern = r'rm\s+(?:-[\w]+\s+|--[\w-]+\s+)*(.+)$'
    match = re.search(path_pattern, command, re.IGNORECASE)

    if not match:
        return False

    path_str = match.group(1).strip()
    paths = path_str.split()

    if not paths:
        return False

    for path in paths:
        path = path.strip('\'"')
        if not path:
            continue

        is_allowed = False
        for allowed_dir in allowed_dirs:
            if path.startswith(allowed_dir) or path.startswith('./' + allowed_dir):
                is_allowed = True
                break

        if not is_allowed:
            return False

    return True


def is_dangerous_rm_command(command, allowed_dirs=None):
    """
    Comprehensive detection of dangerous rm commands.
    Returns False if the command targets only allowed directories.
    """
    if allowed_dirs is None:
        allowed_dirs = []

    normalized = ' '.join(command.lower().split())

    patterns = [
        r'\brm\s+.*-[a-z]*r[a-z]*f',
        r'\brm\s+.*-[a-z]*f[a-z]*r',
        r'\brm\s+--recursive\s+--force',
        r'\brm\s+--force\s+--recursive',
        r'\brm\s+-r\s+.*-f',
        r'\brm\s+-f\s+.*-r',
    ]

    is_potentially_dangerous = False
    for pattern in patterns:
        if re.search(pattern, normalized):
            is_potentially_dangerous = True
            break

    if not is_potentially_dangerous:
        dangerous_paths = [
            r'/',
            r'/\*',
            r'~',
            r'~/',
            r'\$HOME',
            r'\.\.',
            r'\*',
            r'\.',
            r'\.\s*$',
        ]

        if re.search(r'\brm\s+.*-[a-z]*r', normalized):
            for path in dangerous_paths:
                if re.search(path, normalized):
                    is_potentially_dangerous = True
                    break

    if not is_potentially_dangerous:
        return False

    if allowed_dirs and is_path_in_allowed_directory(command, allowed_dirs):
        return False

    return True


def is_env_file_access(tool_name, tool_input):
    """Check if any tool is trying to access .env files containing sensitive data."""
    if tool_name in ['Read', 'Edit', 'MultiEdit', 'Write', 'Bash']:
        if tool_name in ['Read', 'Edit', 'MultiEdit', 'Write']:
            file_path = tool_input.get('file_path', '')
            if '.env' in file_path and not file_path.endswith('.env.sample'):
                return True

        elif tool_name == 'Bash':
            command = tool_input.get('command', '')
            env_patterns = [
                r'\b\.env\b(?!\.sample)',
                r'cat\s+.*\.env\b(?!\.sample)',
                r'echo\s+.*>\s*\.env\b(?!\.sample)',
                r'touch\s+.*\.env\b(?!\.sample)',
                r'cp\s+.*\.env\b(?!\.sample)',
                r'mv\s+.*\.env\b(?!\.sample)',
            ]

            for pattern in env_patterns:
                if re.search(pattern, command):
                    return True

    return False


def deny_tool(reason):
    """Deny a tool call via hookSpecificOutput.permissionDecision."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason
        }
    }
    print(json.dumps(output))
    sys.exit(0)


def run_guards(input_data):
    """Execute security guards. Calls deny_tool() and exits if a violation is found."""
    tool_name = input_data.get('tool_name', '')
    tool_input = input_data.get('tool_input', {})

    # Guard 1: Block .env file access
    if is_env_file_access(tool_name, tool_input):
        deny_tool(
            "Access to .env files containing sensitive data is prohibited. "
            "Use .env.sample for template files instead"
        )

    # Guard 2: Block dangerous rm -rf commands
    if tool_name == 'Bash':
        command = tool_input.get('command', '')
        if is_dangerous_rm_command(command, ALLOWED_RM_DIRECTORIES):
            deny_tool(
                f"Dangerous rm command detected and prevented. "
                f"rm -rf is only allowed in these directories: {', '.join(ALLOWED_RM_DIRECTORIES)}"
            )


def run_logging(input_data):
    """Log tool usage to session log file. Best-effort — failures are swallowed."""
    from pathlib import Path
    from utils.constants import ensure_session_log_dir

    tool_name = input_data.get('tool_name', '')
    tool_input = input_data.get('tool_input', {})
    tool_use_id = input_data.get('tool_use_id', '')
    session_id = input_data.get('session_id', 'unknown')

    log_dir = ensure_session_log_dir(session_id)
    log_path = log_dir / 'pre_tool_use.json'

    if log_path.exists():
        with open(log_path, 'r') as f:
            try:
                log_data = json.load(f)
            except (json.JSONDecodeError, ValueError):
                log_data = []
    else:
        log_data = []

    log_entry = {
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "session_id": session_id,
        "hook_event_name": input_data.get("hook_event_name", "PreToolUse"),
    }

    log_data.append(log_entry)

    with open(log_path, 'w') as f:
        json.dump(log_data, f, indent=2)


try:
    input_data = json.load(sys.stdin)

    # Security guards — these MUST run; deny_tool() exits on violation
    run_guards(input_data)

    # Logging — best-effort, never blocks CC
    try:
        run_logging(input_data)
    except Exception:
        pass

except json.JSONDecodeError:
    pass
except SystemExit:
    raise  # Let deny_tool()'s sys.exit(0) propagate
except Exception:
    pass  # NEVER block CC
