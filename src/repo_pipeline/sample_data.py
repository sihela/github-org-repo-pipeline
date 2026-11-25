"""
Sample Corrupted Dataset and Default GitHub Repository Schema.

Provides:
- Standard GitHub repository ValidationConfig with field rules and cross-field checks.
- Deliberately corrupted sample records to thoroughly test:
  1. Required vs. optional fields (and missing optional data distinction)
  2. Duplicate primary key detection
  3. Data type mismatches
  4. Malformed and invalid URL formats
  5. Empty and whitespace-only values
  6. Special character preservation (emojis, accents, UTF-8 symbols, code chars)
  7. Cross-field date inconsistencies
  8. Negative numeric values
  9. Repairable records (whitespace padding, numeric strings)
"""

import datetime
from typing import Any, Dict, List
from repo_pipeline.validator import FieldRule, Severity, ValidationConfig, ValidationIssue


def parse_iso_date(val: Any) -> Any:
    """Helper to parse ISO-8601 date string for cross-field consistency checks."""
    if not isinstance(val, str):
        return None
    # Strip trailing 'Z' if present
    cleaned = val.replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(cleaned)
    except Exception:
        return None


def cross_field_date_consistency_check(record: Dict[str, Any], index: int) -> List[ValidationIssue]:
    """
    Custom record-level validator:
    Ensures 'created_at' timestamp is not later than 'updated_at' or 'pushed_at'.
    """
    issues: List[ValidationIssue] = []
    record_id = record.get("id", f"<index-{index}>")

    created_at_dt = parse_iso_date(record.get("created_at"))
    updated_at_dt = parse_iso_date(record.get("updated_at"))
    pushed_at_dt = parse_iso_date(record.get("pushed_at"))

    if created_at_dt and updated_at_dt:
        if created_at_dt > updated_at_dt:
            issues.append(
                ValidationIssue(
                    record_id=record_id,
                    field="updated_at",
                    code="INCONSISTENT_DATES",
                    message=(
                        f"Inconsistent timestamps: 'created_at' ({record.get('created_at')}) "
                        f"is later than 'updated_at' ({record.get('updated_at')})."
                    ),
                    severity=Severity.ERROR,
                    actual_value={"created_at": record.get("created_at"), "updated_at": record.get("updated_at")},
                    index=index,
                )
            )

    if created_at_dt and pushed_at_dt:
        if created_at_dt > pushed_at_dt:
            issues.append(
                ValidationIssue(
                    record_id=record_id,
                    field="pushed_at",
                    code="INCONSISTENT_DATES",
                    message=(
                        f"Inconsistent timestamps: 'created_at' ({record.get('created_at')}) "
                        f"is later than 'pushed_at' ({record.get('pushed_at')})."
                    ),
                    severity=Severity.WARNING,
                    actual_value={"created_at": record.get("created_at"), "pushed_at": record.get("pushed_at")},
                    index=index,
                )
            )

    return issues


def get_github_repository_validation_config() -> ValidationConfig:
    """
    Create a production ValidationConfig tailored for GitHub repository metadata.
    """
    config = ValidationConfig(
        primary_key="id",
        allow_unknown_fields=True,
        preserve_special_chars=True,
    )

    # 1. Required Core Fields
    config.add_field_rule(FieldRule(name="id", required=True, expected_type=int, allow_none=False))
    config.add_field_rule(FieldRule(name="name", required=True, expected_type=str, allow_none=False, allow_empty_string=False))
    config.add_field_rule(FieldRule(name="full_name", required=True, expected_type=str, allow_none=False, allow_empty_string=False))
    config.add_field_rule(FieldRule(name="html_url", required=True, expected_type=str, allow_none=False, is_url=True))
    config.add_field_rule(FieldRule(name="clone_url", required=True, expected_type=str, allow_none=False, is_url=True))
    config.add_field_rule(FieldRule(name="default_branch", required=True, expected_type=str, allow_none=False, allow_empty_string=False))

    # 2. Numeric Metrics (Must be non-negative integers)
    config.add_field_rule(FieldRule(name="stargazers_count", required=True, expected_type=int, min_value=0))
    config.add_field_rule(FieldRule(name="forks_count", required=True, expected_type=int, min_value=0))
    config.add_field_rule(FieldRule(name="watchers_count", required=True, expected_type=int, min_value=0))
    config.add_field_rule(FieldRule(name="open_issues_count", required=True, expected_type=int, min_value=0))
    config.add_field_rule(FieldRule(name="size_kb", required=True, expected_type=int, min_value=0))

    # 3. Boolean Flags
    config.add_field_rule(FieldRule(name="is_fork", required=True, expected_type=bool))
    config.add_field_rule(FieldRule(name="is_archived", required=True, expected_type=bool))
    config.add_field_rule(FieldRule(name="is_disabled", required=True, expected_type=bool))

    # 4. Optional Fields (Missing values are classified as INFO, not ERROR)
    config.add_field_rule(FieldRule(name="description", required=False, expected_type=str, allow_none=True))
    config.add_field_rule(FieldRule(name="language", required=False, expected_type=str, allow_none=True))
    config.add_field_rule(FieldRule(name="homepage", required=False, expected_type=str, allow_none=True, is_url=True))
    config.add_field_rule(FieldRule(name="license_name", required=False, expected_type=str, allow_none=True))
    config.add_field_rule(FieldRule(name="license_spdx_id", required=False, expected_type=str, allow_none=True))
    config.add_field_rule(FieldRule(name="topics", required=False, expected_type=list, allow_none=True))
    config.add_field_rule(FieldRule(name="visibility", required=False, expected_type=str, allow_none=True))

    # 5. Timestamps
    config.add_field_rule(FieldRule(name="created_at", required=True, expected_type=str, allow_none=False))
    config.add_field_rule(FieldRule(name="updated_at", required=True, expected_type=str, allow_none=False))
    config.add_field_rule(FieldRule(name="pushed_at", required=False, expected_type=str, allow_none=True))

    # 6. Cross-field custom validator
    config.add_record_validator(cross_field_date_consistency_check)

    return config


def generate_corrupted_sample_dataset() -> List[Dict[str, Any]]:
    """
    Generate a curated list of records containing:
    - Pristine valid records
    - Records testing Unicode / special characters / accents / emoji preservation
    - Records testing optional vs required fields
    - Deliberately corrupted records targeting each validation rule
    """
    return [
        # Record 0: Pristine Valid Record
        {
            "id": 1001,
            "name": "vscode",
            "full_name": "microsoft/vscode",
            "description": "Visual Studio Code editor",
            "language": "TypeScript",
            "html_url": "https://github.com/microsoft/vscode",
            "clone_url": "https://github.com/microsoft/vscode.git",
            "homepage": "https://code.visualstudio.com",
            "default_branch": "main",
            "stargazers_count": 160500,
            "forks_count": 32000,
            "watchers_count": 160500,
            "open_issues_count": 4800,
            "size_kb": 450000,
            "is_fork": False,
            "is_archived": False,
            "is_disabled": False,
            "license_name": "MIT License",
            "license_spdx_id": "MIT",
            "topics": ["editor", "electron", "developer-tools"],
            "created_at": "2015-09-03T20:23:38Z",
            "updated_at": "2026-11-20T10:00:00Z",
            "pushed_at": "2026-11-20T12:00:00Z",
            "visibility": "public",
        },
        # Record 1: Valid Record with Complex Special Characters (Preservation Test)
        # Contains: Japanese, accents, math symbols, emojis, XML chars, quotes
        {
            "id": 1002,
            "name": "cplusplus-sdk_日本語_café™",
            "full_name": "microsoft/cplusplus-sdk_日本語_café™",
            "description": "C++ & C# high-speed engine ⚡🚀 with ∑(x_i) math, <safe-tags>, and quotes: \"'Hello'\"",
            "language": "C++",
            "html_url": "https://github.com/microsoft/cplusplus-sdk",
            "clone_url": "https://github.com/microsoft/cplusplus-sdk.git",
            "homepage": "https://developer.microsoft.com/cplusplus",
            "default_branch": "main",
            "stargazers_count": 520,
            "forks_count": 110,
            "watchers_count": 520,
            "open_issues_count": 12,
            "size_kb": 2500,
            "is_fork": False,
            "is_archived": False,
            "is_disabled": False,
            "license_name": "Apache License 2.0",
            "license_spdx_id": "Apache-2.0",
            "topics": ["cplusplus", "internationalization", "日本語", "café"],
            "created_at": "2018-05-10T14:00:00Z",
            "updated_at": "2026-10-15T09:30:00Z",
            "pushed_at": "2026-10-15T10:00:00Z",
            "visibility": "public",
        },
        # Record 2: Valid Record with Missing Optional Fields (Distinguish from Errors)
        # description, homepage, license_name, topics are all None or omitted
        {
            "id": 1003,
            "name": "internal-utility-tool",
            "full_name": "microsoft/internal-utility-tool",
            "description": None,  # Optional missing data -> INFO
            "language": None,     # Optional missing data -> INFO
            "html_url": "https://github.com/microsoft/internal-utility-tool",
            "clone_url": "https://github.com/microsoft/internal-utility-tool.git",
            "homepage": None,     # Optional missing data -> INFO
            "default_branch": "main",
            "stargazers_count": 15,
            "forks_count": 2,
            "watchers_count": 15,
            "open_issues_count": 0,
            "size_kb": 120,
            "is_fork": False,
            "is_archived": True,
            "is_disabled": False,
            "license_name": None, # Optional missing data -> INFO
            "license_spdx_id": None,
            "topics": [],
            "created_at": "2020-01-15T08:00:00Z",
            "updated_at": "2026-01-10T11:00:00Z",
            "pushed_at": "2026-01-10T11:00:00Z",
            "visibility": "public",
        },
        # Record 3: DELIBERATELY CORRUPTED: Missing Required Field ('name' is missing)
        {
            "id": 1004,
            # 'name' is missing!
            "full_name": "microsoft/unnamed-repo",
            "description": "Repository missing mandatory name attribute",
            "language": "Python",
            "html_url": "https://github.com/microsoft/unnamed-repo",
            "clone_url": "https://github.com/microsoft/unnamed-repo.git",
            "default_branch": "main",
            "stargazers_count": 10,
            "forks_count": 1,
            "watchers_count": 10,
            "open_issues_count": 0,
            "size_kb": 50,
            "is_fork": False,
            "is_archived": False,
            "is_disabled": False,
            "created_at": "2021-03-01T00:00:00Z",
            "updated_at": "2026-03-01T00:00:00Z",
        },
        # Record 4: DELIBERATELY CORRUPTED: Missing Primary Key ('id' is None)
        {
            "id": None,  # Primary key is None!
            "name": "no-id-repo",
            "full_name": "microsoft/no-id-repo",
            "description": "Repository without a valid ID",
            "language": "Go",
            "html_url": "https://github.com/microsoft/no-id-repo",
            "clone_url": "https://github.com/microsoft/no-id-repo.git",
            "default_branch": "main",
            "stargazers_count": 5,
            "forks_count": 0,
            "watchers_count": 5,
            "open_issues_count": 1,
            "size_kb": 80,
            "is_fork": False,
            "is_archived": False,
            "is_disabled": False,
            "created_at": "2021-04-01T00:00:00Z",
            "updated_at": "2026-04-01T00:00:00Z",
        },
        # Record 5: DELIBERATELY CORRUPTED: Duplicate Primary Key (re-uses ID 1001)
        {
            "id": 1001,  # Duplicate of Record 0!
            "name": "vscode-duplicate",
            "full_name": "microsoft/vscode-duplicate",
            "description": "Colliding duplicate ID record",
            "language": "TypeScript",
            "html_url": "https://github.com/microsoft/vscode-duplicate",
            "clone_url": "https://github.com/microsoft/vscode-duplicate.git",
            "default_branch": "main",
            "stargazers_count": 200,
            "forks_count": 30,
            "watchers_count": 200,
            "open_issues_count": 5,
            "size_kb": 1000,
            "is_fork": False,
            "is_archived": False,
            "is_disabled": False,
            "created_at": "2022-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
        # Record 6: DELIBERATELY CORRUPTED: Data Type Mismatch ('stargazers_count' is a string word)
        {
            "id": 1006,
            "name": "bad-type-repo",
            "full_name": "microsoft/bad-type-repo",
            "description": "Stargazers count is a string instead of integer",
            "language": "Rust",
            "html_url": "https://github.com/microsoft/bad-type-repo",
            "clone_url": "https://github.com/microsoft/bad-type-repo.git",
            "default_branch": "main",
            "stargazers_count": "one-thousand-stars",  # Type Mismatch!
            "forks_count": 10,
            "watchers_count": 50,
            "open_issues_count": 2,
            "size_kb": 300,
            "is_fork": False,
            "is_archived": False,
            "is_disabled": False,
            "created_at": "2022-05-01T00:00:00Z",
            "updated_at": "2026-05-01T00:00:00Z",
        },
        # Record 7: DELIBERATELY CORRUPTED: Malformed URL Formats
        {
            "id": 1007,
            "name": "broken-url-repo",
            "full_name": "microsoft/broken-url-repo",
            "description": "Contains invalid URLs",
            "language": "Python",
            "html_url": "httppp://not_a_valid_url_at_all",  # Bad scheme / malformed URL
            "clone_url": "ftp://microsoft/bad.git",          # FTP scheme not permitted
            "homepage": "http://",                           # Missing host
            "default_branch": "main",
            "stargazers_count": 40,
            "forks_count": 5,
            "watchers_count": 40,
            "open_issues_count": 1,
            "size_kb": 200,
            "is_fork": False,
            "is_archived": False,
            "is_disabled": False,
            "created_at": "2022-06-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
        },
        # Record 8: DELIBERATELY CORRUPTED: Empty and Malformed Blank Value
        {
            "id": 1008,
            "name": "   ",  # Blank whitespace-only required name
            "full_name": "microsoft/blank-name-repo",
            "description": "Blank name record",
            "language": "JavaScript",
            "html_url": "https://github.com/microsoft/blank-name",
            "clone_url": "https://github.com/microsoft/blank-name.git",
            "default_branch": "main",
            "stargazers_count": 10,
            "forks_count": 2,
            "watchers_count": 10,
            "open_issues_count": 0,
            "size_kb": 100,
            "is_fork": False,
            "is_archived": False,
            "is_disabled": False,
            "created_at": "2022-07-01T00:00:00Z",
            "updated_at": "2026-07-01T00:00:00Z",
        },
        # Record 9: DELIBERATELY CORRUPTED: Inconsistent Record (Negative metrics & inverted dates)
        {
            "id": 1009,
            "name": "inconsistent-chronology-repo",
            "full_name": "microsoft/inconsistent-chronology-repo",
            "description": "Chronology is backwards and fork count is negative",
            "language": "Python",
            "html_url": "https://github.com/microsoft/inconsistent-repo",
            "clone_url": "https://github.com/microsoft/inconsistent-repo.git",
            "default_branch": "main",
            "stargazers_count": 100,
            "forks_count": -50,  # Negative count!
            "watchers_count": 100,
            "open_issues_count": -1,  # Negative count!
            "size_kb": 400,
            "is_fork": False,
            "is_archived": False,
            "is_disabled": False,
            # 'created_at' is 2026, but 'updated_at' is 2012 (impossible backwards time)
            "created_at": "2026-11-20T10:00:00Z",
            "updated_at": "2012-01-01T00:00:00Z",
            "pushed_at": "2010-01-01T00:00:00Z",
        },
        # Record 10: FIXABLE / REPAIRABLE RECORD (Cleaner Test)
        # Has leading/trailing whitespace, numeric string, and empty string optional field
        # Preserves special characters inside name and description: "PowerToys ✨ (日本語)"
        {
            "id": 1010,
            "name": "  PowerToys ✨ (日本語)  ",  # fixable whitespace padding + special chars
            "full_name": "  microsoft/PowerToys  ",
            "description": "  System utilities for Windows power users 🛠️  ",
            "language": "C++",
            "html_url": "https://github.com/microsoft/PowerToys",
            "clone_url": "https://github.com/microsoft/PowerToys.git",
            "homepage": "",  # Empty string for optional field -> fixable to None
            "default_branch": "main",
            "stargazers_count": "105000",  # String integer -> fixable to int(105000)
            "forks_count": 6200,
            "watchers_count": 105000,
            "open_issues_count": 800,
            "size_kb": 120000,
            "is_fork": False,
            "is_archived": False,
            "is_disabled": False,
            "license_name": "MIT License",
            "license_spdx_id": "MIT",
            "topics": ["windows", "powertoys", "utility"],
            "created_at": "2019-05-01T00:00:00Z",
            "updated_at": "2026-11-20T00:00:00Z",
            "pushed_at": "2026-11-20T00:00:00Z",
            "visibility": "public",
        },
    ]
