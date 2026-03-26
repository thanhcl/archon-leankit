#!/usr/bin/env python3
"""
Migration verification script for archon-leankit.

Verifies:
1. No duplicate migration file numbers
2. All expected tables exist with correct columns
3. All foreign keys present
4. All indexes present
5. Migration tracking records are consistent

Usage:
  python scripts/verify_migrations.py
  DB_CONTAINER=supabase-db python scripts/verify_migrations.py
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
MIGRATION_DIRS = [
    ROOT_DIR / "migration" / "0.1.0",
    ROOT_DIR / "migration" / "0.1.0-leankit",
]
DB_CONTAINER = os.environ.get("DB_CONTAINER", "supabase-db")
DB_USER = os.environ.get("DB_USER", "supabase_admin")
DB_NAME = os.environ.get("DB_NAME", "postgres")

# --- Expected schema definition ---

EXPECTED_TABLES: dict[str, list[str]] = {
    # Core knowledge tables (from complete_setup.sql and base migrations)
    "archon_settings": ["id", "key", "value", "encrypted_value", "is_encrypted", "category", "description", "created_at", "updated_at"],
    "archon_sources": ["source_id", "source_url", "source_display_name", "summary", "total_word_count", "title", "metadata", "created_at", "updated_at"],
    "archon_crawled_pages": [
        "id", "url", "chunk_number", "content", "metadata", "source_id", "page_id",
        "embedding_384", "embedding_768", "embedding_1024", "embedding_1536", "embedding_3072",
        "llm_chat_model", "embedding_model", "embedding_dimension", "content_search_vector", "created_at",
    ],
    "archon_code_examples": [
        "id", "url", "chunk_number", "content", "summary", "metadata", "source_id",
        "embedding_384", "embedding_768", "embedding_1024", "embedding_1536", "embedding_3072",
        "llm_chat_model", "embedding_model", "embedding_dimension", "content_search_vector", "created_at",
    ],
    "archon_page_metadata": [
        "id", "source_id", "url", "full_content", "section_title", "section_order",
        "word_count", "char_count", "chunk_count", "metadata", "created_at", "updated_at",
    ],
    "archon_prompts": ["id", "prompt_name", "prompt", "description", "created_at", "updated_at"],
    # Projects & Tasks
    "archon_projects": [
        "id", "title", "description", "docs", "features", "data", "github_repo", "pinned",
        "source_app", "layout_id", "team_config", "director_config", "team_lead_config", "office_settings",
        "created_at", "updated_at",
    ],
    "archon_tasks": [
        "id", "project_id", "parent_task_id", "title", "description", "status", "assignee",
        "task_order", "priority", "feature", "sources", "code_examples", "archived", "archived_at", "archived_by",
        # Lifecycle fields (transition migrations)
        "state_changed_at", "state_changed_by", "owner", "rejection_reason", "hold_reason",
        "state_history", "blocked_by", "acceptance_criteria", "execution_result", "architect_review",
        "retry_count", "max_retries", "execution_prompt", "source_app", "complexity",
        # Review & extended fields
        "review_history", "task_type", "phase", "module", "sprint",
        # LeanKit 015-030 additions
        "allowed_paths", "forbidden_paths", "repo_guidance_packs", "plan_item_id", "tags",
        "owner_rating", "owner_notes", "improvement_tags", "pipeline_steps",
        "created_at", "updated_at",
    ],
    "archon_project_sources": ["id", "project_id", "source_id", "linked_at", "created_by", "notes"],
    "archon_document_versions": [
        "id", "project_id", "task_id", "field_name", "version_number", "content",
        "change_summary", "change_type", "document_id", "created_by", "created_at",
    ],
    # Learning & Code Patterns
    "archon_learnings": [
        "id", "project_id", "task_id", "type", "description", "area", "suggested_rule",
        "pattern_key", "recurrence_count", "first_seen", "last_seen", "related_learnings",
        "related_tasks", "status", "promoted_to", "promoted_at", "source_run_ids", "created_at",
    ],
    "archon_code_patterns": [
        "id", "project_id", "pattern_name", "pattern_key", "category", "language",
        "code_example", "context", "anti_pattern", "source_task_ids", "source_files",
        "extracted_from", "usage_count", "last_used_at", "confidence", "expert_validated",
        "status", "promoted_to", "created_at", "updated_at",
    ],
    # Rules
    "archon_rules": [
        "id", "project_id", "section", "rule_text", "priority", "source", "enabled",
        "created_at", "updated_at",
    ],
    "archon_rule_suggestions": [
        "id", "project_id", "learning_id", "section", "rule_text", "confidence",
        "reason", "status", "created_at", "updated_at",
    ],
    # Migration tracking
    "archon_migrations": ["id", "version", "migration_name", "applied_at", "checksum"],
    # LeanKit 012+ tables
    "archon_execution_runs": [
        "id", "task_id", "project_id", "engine_id", "session_id", "status", "stage",
        "model", "retry_index", "started_at", "finished_at", "duration_seconds",
        "token_input", "token_output", "cost_usd", "result_summary", "error_summary",
        "metadata", "total_tokens", "thinking_tokens", "workspace_path",
        "created_at", "updated_at",
    ],
    "archon_bootstrap_plans": [
        "id", "project_id", "requested_provider", "resolved_provider", "strategy",
        "model", "template", "project_type", "bootstrap_policy", "source_app",
        "status", "plan_items", "created_tasks", "metadata", "created_at", "updated_at",
    ],
    "archon_external_requests": [
        "id", "project_id", "task_id", "execution_run_id", "bootstrap_plan_id",
        "source_channel", "request_type", "status", "materialize_as", "title", "summary",
        "correlation_id", "source_app", "actor_id", "actor_display", "payload",
        "linked_task_id", "linked_approval_request_id", "created_at", "updated_at",
    ],
    "archon_approval_requests": [
        "id", "project_id", "task_id", "execution_run_id", "bootstrap_plan_id",
        "external_request_id", "status", "title", "summary", "requested_by",
        "requested_channel", "actor_id", "actor_display", "context",
        "decided_by", "decision_comment", "decided_at", "created_at", "updated_at",
    ],
    "archon_engine_policies": [
        "id", "project_id", "is_active", "model_routing", "retry_policy",
        "budget_policy", "isolation_policy", "review_policy", "capacity_policy",
        "created_at", "updated_at",
    ],
    "project_implementation_plans": [
        "id", "project_id", "title", "description", "status", "created_by",
        "source_doc_hash", "source_doc_path", "metadata", "created_at", "updated_at",
    ],
    "project_implementation_phases": [
        "id", "plan_id", "title", "description", "phase_order", "metadata",
        "created_at", "updated_at",
    ],
    "project_implementation_items": [
        "id", "plan_id", "phase_id", "title", "description", "status", "item_order",
        "priority", "complexity", "item_key", "metadata", "created_at", "updated_at",
    ],
    "project_implementation_item_dependencies": [
        "id", "dependent_id", "dependency_id", "dependency_type", "metadata", "created_at",
    ],
    "project_implementation_item_task_links": [
        "id", "item_id", "task_id", "link_type", "created_at",
    ],
    "project_implementation_snapshots": [
        "id", "plan_id", "label", "snapshot", "created_by", "created_at",
    ],
    "archon_agent_definitions": [
        "id", "slug", "name", "description", "capabilities", "model_preferences",
        "prompt_template", "is_active", "created_at", "updated_at",
    ],
    "archon_promotion_log": [
        "id", "learning_id", "project_id", "promotion_type", "promoted_to",
        "recurrence_count", "source_run_ids", "source_task_ids", "confidence",
        "reason", "learning_description", "created_at",
    ],
}

# Key foreign keys to verify: (table, column, referenced_table, referenced_column)
EXPECTED_FOREIGN_KEYS: list[tuple[str, str, str, str]] = [
    ("archon_tasks", "project_id", "archon_projects", "id"),
    ("archon_tasks", "parent_task_id", "archon_tasks", "id"),
    ("archon_tasks", "plan_item_id", "project_implementation_items", "id"),
    ("archon_execution_runs", "task_id", "archon_tasks", "id"),
    ("archon_execution_runs", "project_id", "archon_projects", "id"),
    ("archon_bootstrap_plans", "project_id", "archon_projects", "id"),
    ("archon_external_requests", "project_id", "archon_projects", "id"),
    ("archon_external_requests", "task_id", "archon_tasks", "id"),
    ("archon_external_requests", "execution_run_id", "archon_execution_runs", "id"),
    ("archon_approval_requests", "project_id", "archon_projects", "id"),
    ("archon_approval_requests", "task_id", "archon_tasks", "id"),
    ("archon_approval_requests", "execution_run_id", "archon_execution_runs", "id"),
    ("archon_engine_policies", "project_id", "archon_projects", "id"),
    ("archon_learnings", "project_id", "archon_projects", "id"),
    ("archon_learnings", "task_id", "archon_tasks", "id"),
    ("archon_code_patterns", "project_id", "archon_projects", "id"),
    ("archon_rules", "project_id", "archon_projects", "id"),
    ("archon_rule_suggestions", "project_id", "archon_projects", "id"),
    ("archon_rule_suggestions", "learning_id", "archon_learnings", "id"),
    ("archon_promotion_log", "learning_id", "archon_learnings", "id"),
    ("archon_promotion_log", "project_id", "archon_projects", "id"),
    ("project_implementation_plans", "project_id", "archon_projects", "id"),
    ("project_implementation_phases", "plan_id", "project_implementation_plans", "id"),
    ("project_implementation_items", "plan_id", "project_implementation_plans", "id"),
    ("project_implementation_items", "phase_id", "project_implementation_phases", "id"),
    ("project_implementation_item_dependencies", "dependent_id", "project_implementation_items", "id"),
    ("project_implementation_item_dependencies", "dependency_id", "project_implementation_items", "id"),
    ("project_implementation_item_task_links", "item_id", "project_implementation_items", "id"),
    ("project_implementation_item_task_links", "task_id", "archon_tasks", "id"),
    ("project_implementation_snapshots", "plan_id", "project_implementation_plans", "id"),
    ("archon_crawled_pages", "source_id", "archon_sources", "source_id"),
    ("archon_code_examples", "source_id", "archon_sources", "source_id"),
    ("archon_crawled_pages", "page_id", "archon_page_metadata", "id"),
    ("archon_page_metadata", "source_id", "archon_sources", "source_id"),
]

# Key indexes to verify: (table, index_name_pattern)
EXPECTED_INDEXES: list[tuple[str, str]] = [
    ("archon_execution_runs", "idx_execution_runs_task_id"),
    ("archon_execution_runs", "idx_execution_runs_project_id"),
    ("archon_execution_runs", "idx_execution_runs_status"),
    ("archon_execution_runs", "idx_execution_runs_stage"),
    ("archon_execution_runs", "idx_execution_runs_started_at"),
    ("archon_bootstrap_plans", "idx_archon_bootstrap_plans_project_id"),
    ("archon_external_requests", "idx_archon_external_requests_project_id"),
    ("archon_external_requests", "idx_archon_external_requests_status"),
    ("archon_external_requests", "idx_archon_external_requests_correlation_id"),
    ("archon_approval_requests", "idx_archon_approval_requests_project_id"),
    ("archon_approval_requests", "idx_archon_approval_requests_status"),
    ("archon_engine_policies", "idx_engine_policies_project_id"),
    ("archon_engine_policies", "idx_engine_policies_is_active"),
    ("archon_tasks", "idx_archon_tasks_plan_item_id"),
    ("archon_tasks", "idx_archon_tasks_tags"),
    ("archon_tasks", "idx_archon_tasks_owner_rating"),
    ("archon_agent_definitions", "idx_agent_definitions_slug"),
    ("archon_agent_definitions", "idx_agent_definitions_is_active"),
    ("archon_promotion_log", "idx_archon_promotion_log_learning_id"),
    ("archon_promotion_log", "idx_archon_promotion_log_project_id"),
    ("project_implementation_plans", "idx_impl_plans_project_id"),
    ("project_implementation_plans", "idx_impl_plans_status"),
    ("project_implementation_phases", "idx_impl_phases_plan_id"),
    ("project_implementation_items", "idx_impl_items_plan_id"),
    ("project_implementation_items", "idx_impl_items_status"),
    ("project_implementation_item_dependencies", "idx_impl_deps_dependent_id"),
    ("project_implementation_item_dependencies", "idx_impl_deps_dependency_id"),
    ("project_implementation_item_task_links", "idx_impl_task_links_item_id"),
    ("project_implementation_item_task_links", "idx_impl_task_links_task_id"),
    ("project_implementation_snapshots", "idx_impl_snapshots_plan_id"),
]


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def run_psql(query: str) -> str:
    """Execute a SQL query against the local Supabase database via Docker."""
    result = subprocess.run(
        [
            "docker", "exec", "-i", DB_CONTAINER,
            "psql", "-U", DB_USER, "-d", DB_NAME,
            "-t", "-A", "-c", query,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql error: {result.stderr.strip()}")
    return result.stdout.strip()


def check_docker_running() -> bool:
    """Check if the database container is running."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
        return DB_CONTAINER in result.stdout.split("\n")
    except Exception:
        return False


# ── File-system checks ──


def verify_migration_file_numbering() -> list[str]:
    """Check for duplicate or missing migration file numbers."""
    issues: list[str] = []

    for mdir in MIGRATION_DIRS:
        if not mdir.exists():
            issues.append(f"Migration directory missing: {mdir}")
            continue

        numbers: dict[str, list[str]] = {}
        for f in sorted(mdir.glob("*.sql")):
            match = re.match(r"^(\d+)_", f.name)
            if match:
                num = match.group(1)
                numbers.setdefault(num, []).append(f.name)

        for num, files in sorted(numbers.items()):
            if len(files) > 1:
                issues.append(f"DUPLICATE number {num} in {mdir.name}: {', '.join(files)}")

        # Check for gaps
        all_nums = sorted(int(n) for n in numbers)
        if all_nums:
            for i in range(len(all_nums) - 1):
                if all_nums[i + 1] - all_nums[i] > 1:
                    gap_start = all_nums[i] + 1
                    gap_end = all_nums[i + 1] - 1
                    gap = f"{gap_start}" if gap_start == gap_end else f"{gap_start}-{gap_end}"
                    issues.append(f"Gap in numbering in {mdir.name}: missing {gap}")

    return issues


# ── Database checks ──


def verify_tables() -> tuple[list[str], list[str]]:
    """Verify all expected tables exist and have correct columns."""
    issues: list[str] = []
    warnings: list[str] = []

    # Get all public tables
    existing_tables_raw = run_psql(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name;"
    )
    existing_tables = set(existing_tables_raw.split("\n")) if existing_tables_raw else set()

    for table, expected_cols in EXPECTED_TABLES.items():
        if table not in existing_tables:
            issues.append(f"Table MISSING: {table}")
            continue

        # Get actual columns
        actual_cols_raw = run_psql(
            f"SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema = 'public' AND table_name = '{table}' "
            f"ORDER BY ordinal_position;"
        )
        actual_cols = set(actual_cols_raw.split("\n")) if actual_cols_raw else set()

        # Deduplicate expected (archon_tasks has duplicate created_at/updated_at in definition)
        expected_set = set(expected_cols)

        missing = expected_set - actual_cols
        extra = actual_cols - expected_set

        for col in sorted(missing):
            issues.append(f"Column MISSING: {table}.{col}")
        for col in sorted(extra):
            # Extra columns are warnings, not errors (could be from other migrations)
            if col not in ("embedding",):  # Known legacy columns
                warnings.append(f"Extra column: {table}.{col}")

    return issues, warnings


def verify_foreign_keys() -> list[str]:
    """Verify expected foreign keys exist."""
    issues: list[str] = []

    fk_query = """
    SELECT
        tc.table_name,
        kcu.column_name,
        ccu.table_name AS foreign_table_name,
        ccu.column_name AS foreign_column_name
    FROM information_schema.table_constraints AS tc
    JOIN information_schema.key_column_usage AS kcu
        ON tc.constraint_name = kcu.constraint_name
        AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage AS ccu
        ON ccu.constraint_name = tc.constraint_name
        AND ccu.table_schema = tc.table_schema
    WHERE tc.constraint_type = 'FOREIGN KEY'
        AND tc.table_schema = 'public'
    ORDER BY tc.table_name, kcu.column_name;
    """

    fk_raw = run_psql(fk_query)
    existing_fks: set[tuple[str, str, str, str]] = set()
    if fk_raw:
        for line in fk_raw.split("\n"):
            parts = line.split("|")
            if len(parts) == 4:
                existing_fks.add((parts[0], parts[1], parts[2], parts[3]))

    for table, col, ref_table, ref_col in EXPECTED_FOREIGN_KEYS:
        if (table, col, ref_table, ref_col) not in existing_fks:
            issues.append(f"FK MISSING: {table}.{col} -> {ref_table}.{ref_col}")

    return issues


def verify_indexes() -> list[str]:
    """Verify expected indexes exist."""
    issues: list[str] = []

    idx_raw = run_psql(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' ORDER BY indexname;"
    )
    existing_indexes = set(idx_raw.split("\n")) if idx_raw else set()

    for table, idx_name in EXPECTED_INDEXES:
        if idx_name not in existing_indexes:
            issues.append(f"Index MISSING: {idx_name} on {table}")

    return issues


def verify_migration_tracking() -> tuple[list[str], list[str]]:
    """Verify migration tracking records are consistent with files on disk."""
    issues: list[str] = []
    info: list[str] = []

    # Get recorded migrations
    recorded_raw = run_psql(
        "SELECT version || '/' || migration_name FROM archon_migrations ORDER BY version, migration_name;"
    )
    recorded = set(recorded_raw.split("\n")) if recorded_raw else set()

    # Get migration files on disk
    on_disk: set[str] = set()
    for mdir in MIGRATION_DIRS:
        if not mdir.exists():
            continue
        version = mdir.name
        for f in sorted(mdir.glob("*.sql")):
            on_disk.add(f"{version}/{f.stem}")

    # Also include 0.1.0 base migrations
    base_dir = ROOT_DIR / "migration" / "0.1.0"
    if base_dir.exists():
        for f in sorted(base_dir.glob("*.sql")):
            on_disk.add(f"0.1.0/{f.stem}")

    unrecorded = on_disk - recorded
    orphaned = recorded - on_disk

    for m in sorted(unrecorded):
        info.append(f"Migration file not recorded in tracking table: {m}")
    for m in sorted(orphaned):
        issues.append(f"Recorded migration has no file on disk: {m}")

    return issues, info


# ── Main ──


def main() -> int:
    print(f"\n{Colors.BOLD}{'='*60}")
    print("  Archon-LeanKit Migration Verification")
    print(f"{'='*60}{Colors.RESET}\n")

    all_issues: list[str] = []
    all_warnings: list[str] = []

    # 1. File numbering
    print(f"{Colors.CYAN}[1/5] Checking migration file numbering...{Colors.RESET}")
    file_issues = verify_migration_file_numbering()
    all_issues.extend(file_issues)
    if file_issues:
        for i in file_issues:
            print(f"  {Colors.RED}FAIL{Colors.RESET} {i}")
    else:
        print(f"  {Colors.GREEN}PASS{Colors.RESET} No duplicate or missing migration numbers")

    # Check database connectivity
    if not check_docker_running():
        print(f"\n{Colors.YELLOW}Database container '{DB_CONTAINER}' is not running.")
        print(f"Skipping database checks. Start Supabase with: docker compose up -d{Colors.RESET}")
        if all_issues:
            print(f"\n{Colors.RED}File-system issues found: {len(all_issues)}{Colors.RESET}")
            return 1
        print(f"\n{Colors.GREEN}File-system checks passed.{Colors.RESET}")
        return 0

    # 2. Tables & columns
    print(f"\n{Colors.CYAN}[2/5] Verifying tables and columns...{Colors.RESET}")
    table_issues, table_warnings = verify_tables()
    all_issues.extend(table_issues)
    all_warnings.extend(table_warnings)
    if table_issues:
        for i in table_issues:
            print(f"  {Colors.RED}FAIL{Colors.RESET} {i}")
    else:
        print(f"  {Colors.GREEN}PASS{Colors.RESET} All {len(EXPECTED_TABLES)} tables present with expected columns")
    if table_warnings:
        for w in table_warnings:
            print(f"  {Colors.YELLOW}WARN{Colors.RESET} {w}")

    # 3. Foreign keys
    print(f"\n{Colors.CYAN}[3/5] Verifying foreign keys...{Colors.RESET}")
    fk_issues = verify_foreign_keys()
    all_issues.extend(fk_issues)
    if fk_issues:
        for i in fk_issues:
            print(f"  {Colors.RED}FAIL{Colors.RESET} {i}")
    else:
        print(f"  {Colors.GREEN}PASS{Colors.RESET} All {len(EXPECTED_FOREIGN_KEYS)} foreign keys present")

    # 4. Indexes
    print(f"\n{Colors.CYAN}[4/5] Verifying indexes...{Colors.RESET}")
    idx_issues = verify_indexes()
    all_issues.extend(idx_issues)
    if idx_issues:
        for i in idx_issues:
            print(f"  {Colors.RED}FAIL{Colors.RESET} {i}")
    else:
        print(f"  {Colors.GREEN}PASS{Colors.RESET} All {len(EXPECTED_INDEXES)} indexes present")

    # 5. Migration tracking
    print(f"\n{Colors.CYAN}[5/5] Verifying migration tracking records...{Colors.RESET}")
    track_issues, track_info = verify_migration_tracking()
    all_issues.extend(track_issues)
    if track_issues:
        for i in track_issues:
            print(f"  {Colors.RED}FAIL{Colors.RESET} {i}")
    if track_info:
        for i in track_info:
            print(f"  {Colors.YELLOW}INFO{Colors.RESET} {i}")
    if not track_issues and not track_info:
        print(f"  {Colors.GREEN}PASS{Colors.RESET} All migration files recorded in tracking table")

    # Summary
    print(f"\n{Colors.BOLD}{'='*60}")
    print("  Summary")
    print(f"{'='*60}{Colors.RESET}")
    print(f"  Issues:   {len(all_issues)}")
    print(f"  Warnings: {len(all_warnings)}")

    if all_issues:
        print(f"\n  {Colors.RED}VERIFICATION FAILED{Colors.RESET} — {len(all_issues)} issue(s) found")
        return 1
    else:
        print(f"\n  {Colors.GREEN}VERIFICATION PASSED{Colors.RESET}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
