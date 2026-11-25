"""
GitHub Repository Pipeline Package.

A modular, high-resilience pipeline for scraping, validating, and cleaning
repository metadata using the official GitHub REST API.
"""

from typing import Any

__version__ = "0.1.0"

__all__ = [
    "GitHubAPIError",
    "GitHubClient",
    "GitHubRateLimitExceeded",
    "extract_repository_metadata",
    "extract_batch",
    "FieldRule",
    "Severity",
    "ValidationConfig",
    "ValidationIssue",
    "ValidationReport",
    "Validator",
    "generate_corrupted_sample_dataset",
    "get_github_repository_validation_config",
    "CleaningReport",
    "DataCleaner",
    "export_dataset_csv",
    "export_dataset_json",
    "load_dataset",
]

_EXPORTS = {
    "extract_repository_metadata": ("repo_pipeline.extractor", "extract_repository_metadata"),
    "extract_batch": ("repo_pipeline.extractor", "extract_batch"),
    "GitHubAPIError": ("repo_pipeline.github_client", "GitHubAPIError"),
    "GitHubClient": ("repo_pipeline.github_client", "GitHubClient"),
    "GitHubRateLimitExceeded": ("repo_pipeline.github_client", "GitHubRateLimitExceeded"),
    "FieldRule": ("repo_pipeline.validator", "FieldRule"),
    "Severity": ("repo_pipeline.validator", "Severity"),
    "ValidationConfig": ("repo_pipeline.validator", "ValidationConfig"),
    "ValidationIssue": ("repo_pipeline.validator", "ValidationIssue"),
    "ValidationReport": ("repo_pipeline.validator", "ValidationReport"),
    "Validator": ("repo_pipeline.validator", "Validator"),
    "generate_corrupted_sample_dataset": ("repo_pipeline.sample_data", "generate_corrupted_sample_dataset"),
    "get_github_repository_validation_config": ("repo_pipeline.sample_data", "get_github_repository_validation_config"),
    "CleaningReport": ("repo_pipeline.cleaner", "CleaningReport"),
    "DataCleaner": ("repo_pipeline.cleaner", "DataCleaner"),
    "export_dataset_csv": ("repo_pipeline.cleaner", "export_dataset_csv"),
    "export_dataset_json": ("repo_pipeline.cleaner", "export_dataset_json"),
    "load_dataset": ("repo_pipeline.cleaner", "load_dataset"),
}


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        mod_name, attr_name = _EXPORTS[name]
        import importlib
        mod = importlib.import_module(mod_name)
        val = getattr(mod, attr_name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals().keys()) + list(_EXPORTS.keys()))
