"""
Repository Metadata Extractor.

Extracts structured repository metadata from GitHub API responses including
name, description, primary language, URLs, stars, forks, licenses, topics,
and timestamps.
"""

from typing import Any, Dict, List, Optional


def extract_repository_metadata(repo_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract relevant metadata fields from a GitHub repository dictionary.

    :param repo_data: Raw repository dictionary from GitHub API.
    :return: Cleaned and structured metadata dictionary.
    """
    license_info = repo_data.get("license") or {}
    license_name = license_info.get("name") if isinstance(license_info, dict) else None
    license_spdx = license_info.get("spdx_id") if isinstance(license_info, dict) else None

    # Handle license SPDX 'NOASSERTION' or None
    if license_spdx in ("NOASSERTION", ""):
        license_spdx = None

    topics = repo_data.get("topics") or []

    return {
        "id": repo_data.get("id"),
        "name": repo_data.get("name"),
        "full_name": repo_data.get("full_name"),
        "description": repo_data.get("description"),
        "language": repo_data.get("language"),
        "html_url": repo_data.get("html_url"),
        "clone_url": repo_data.get("clone_url"),
        "homepage": repo_data.get("homepage") or None,
        "default_branch": repo_data.get("default_branch"),
        "stargazers_count": repo_data.get("stargazers_count", 0),
        "forks_count": repo_data.get("forks_count", 0),
        "watchers_count": repo_data.get("watchers_count", 0),
        "open_issues_count": repo_data.get("open_issues_count", 0),
        "size_kb": repo_data.get("size", 0),
        "is_fork": repo_data.get("fork", False),
        "is_archived": repo_data.get("archived", False),
        "is_disabled": repo_data.get("disabled", False),
        "license_name": license_name,
        "license_spdx_id": license_spdx,
        "topics": topics,
        "created_at": repo_data.get("created_at"),
        "updated_at": repo_data.get("updated_at"),
        "pushed_at": repo_data.get("pushed_at"),
        "visibility": repo_data.get("visibility", "public"),
    }


def extract_batch(repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Extract metadata for a list of repository dictionaries.

    :param repos: List of raw repository dictionaries.
    :return: List of structured metadata dictionaries.
    """
    return [extract_repository_metadata(r) for r in repos]
