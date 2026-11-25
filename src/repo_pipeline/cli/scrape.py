#!/usr/bin/env python3
"""
GitHub Public Repositories Fetcher.

Fetches all public repositories for an organization (e.g., 'microsoft')
using the official GitHub REST API with robust pagination, rate-limit
handling, retries with exponential backoff, configurable request delays,
checkpointing/resume support, graceful interruption, and metadata extraction.

Optimized for resource-constrained environments (low RAM and CPU usage)
with streaming I/O, on-the-fly statistics, and guaranteed resource cleanup.
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from repo_pipeline.extractor import extract_repository_metadata
from repo_pipeline.github_client import GitHubAPIError, GitHubClient, GitHubRateLimitExceeded


def configure_logging(level_name: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    """
    Configure root and application logging with appropriate formatters.
    """
    level = getattr(logging, level_name.upper(), logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File Handler (optional)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    return logging.getLogger("main")


class CheckpointManager:
    """
    Manages lightweight, atomic checkpointing for scraper pagination state.
    Allows resuming interrupted jobs from the exact last-fetched page.
    """

    def __init__(self, checkpoint_file: str):
        self.checkpoint_file = checkpoint_file

    def exists(self) -> bool:
        """Check if a checkpoint file exists."""
        return os.path.isfile(self.checkpoint_file)

    def load(self) -> Optional[Dict[str, Any]]:
        """Load checkpoint state from disk."""
        if not self.exists():
            return None
        try:
            with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logging.getLogger("main").warning("Failed to load checkpoint file '%s': %s", self.checkpoint_file, exc)
            return None

    def save(
        self,
        org: str,
        page_num: int,
        next_url: Optional[str],
        total_fetched: int,
        output_file: str,
        fmt: str
    ) -> None:
        """Save pagination checkpoint atomically."""
        state = {
            "org": org,
            "page_num": page_num,
            "next_url": next_url,
            "total_fetched": total_fetched,
            "output_file": output_file,
            "format": fmt,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        temp_file = f"{self.checkpoint_file}.tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.replace(temp_file, self.checkpoint_file)
            logging.getLogger("main").debug("Saved checkpoint: page=%d, total_fetched=%d", page_num, total_fetched)
        except Exception as exc:
            logging.getLogger("main").warning("Could not save checkpoint to '%s': %s", self.checkpoint_file, exc)
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass

    def remove(self) -> None:
        """Remove checkpoint file upon successful job completion."""
        if self.exists():
            try:
                os.remove(self.checkpoint_file)
                logging.getLogger("main").debug("Checkpoint file removed: %s", self.checkpoint_file)
            except OSError as exc:
                logging.getLogger("main").warning("Could not remove checkpoint file: %s", exc)


class StreamingWriter:
    """
    Low-memory streaming writer supporting JSON and CSV.
    Flushes items progressively to disk, minimizing memory footprint on 8GB machines.
    Supports resuming existing partial files without corruption or duplicate headers.
    """

    def __init__(self, filepath: str, fmt: str, resume: bool = False, existing_count: int = 0):
        self.filepath = filepath
        self.fmt = fmt.lower()
        self.count = existing_count
        self.csv_writer: Optional[csv.DictWriter] = None
        self.is_resuming = resume and os.path.exists(filepath) and os.path.getsize(filepath) > 0

        if self.is_resuming:
            if self.fmt == "csv":
                self.file = open(filepath, "a", newline="", encoding="utf-8")
            else:
                # JSON resume: open in r+, strip trailing ']' and prepare to append
                with open(filepath, "r", encoding="utf-8") as rf:
                    content = rf.read().rstrip()
                if content.endswith("]"):
                    content = content[:-1].rstrip()
                with open(filepath, "w", encoding="utf-8") as wf:
                    wf.write(content)
                self.file = open(filepath, "a", encoding="utf-8")
        else:
            self.file = open(filepath, "w", newline="", encoding="utf-8")
            if self.fmt == "json":
                self.file.write("[\n")
            self.count = 0

    def write_item(self, item: Dict[str, Any]) -> None:
        """Write a single item to the file stream."""
        if self.fmt == "csv":
            if self.csv_writer is None:
                fieldnames = list(item.keys())
                self.csv_writer = csv.DictWriter(self.file, fieldnames=fieldnames)
                if not self.is_resuming and self.count == 0:
                    self.csv_writer.writeheader()

            row_copy = item.copy()
            # Convert topics list to semicolon-separated string for CSV
            if isinstance(row_copy.get("topics"), list):
                row_copy["topics"] = "; ".join(row_copy["topics"])
            self.csv_writer.writerow(row_copy)
        else:
            # JSON format
            serialized = json.dumps(item, indent=2, ensure_ascii=False)
            indented = "  " + serialized.replace("\n", "\n  ")
            if self.count > 0:
                self.file.write(",\n")
            self.file.write(indented)

        self.count += 1
        if self.count % 50 == 0:
            self.file.flush()

    def close(self) -> None:
        """Finalize and close the file."""
        if self.fmt == "json":
            self.file.write("\n]\n")
        self.file.flush()
        self.file.close()


def print_summary(
    total_count: int,
    total_stars: int,
    total_forks: int,
    languages: Counter,
    top_starred: List[Dict[str, Any]],
    org: str,
    elapsed_time: float
) -> None:
    """Print statistical summary of retrieved repositories."""
    if total_count == 0:
        print(f"\nNo repositories retrieved for '{org}'.")
        return

    print("\n" + "=" * 60)
    print(f" Summary for Organization: {org}")
    print("=" * 60)
    print(f"Total Repositories Fetched: {total_count:,}")
    print(f"Elapsed Time:             {elapsed_time:.2f} seconds")
    print(f"Total Stars:              {total_stars:,}")
    print(f"Total Forks:              {total_forks:,}")
    print("-" * 60)
    print("Top 5 Languages:")
    for lang, count in languages.most_common(5):
        pct = (count / total_count) * 100
        print(f"  - {lang:20s}: {count:5d} repos ({pct:5.1f}%)")
    print("-" * 60)
    print("Top 5 Starred Repositories:")
    for repo in top_starred:
        print(f"  - {repo['name']:30s} ★ {repo.get('stargazers_count', 0):,}")
    print("=" * 60 + "\n")


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Fetch all public repositories for an organization using official GitHub REST API.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--org",
        default="microsoft",
        help="GitHub organization name to retrieve repositories for."
    )
    parser.add_argument(
        "--output",
        "-o",
        default="microsoft_repositories.json",
        help="Output file path (supports .json and .csv extensions)."
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default=None,
        help="Output file format (defaults to extension inferred from --output)."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of repositories to fetch. If omitted, fetches all."
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=100,
        help="Number of records per page request (GitHub max: 100)."
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.0,
        help="Optional polite pacing delay in seconds between consecutive page requests."
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume fetching from the last saved checkpoint if available."
    )
    parser.add_argument(
        "--checkpoint-file",
        default=None,
        help="Path to checkpoint state file (defaults to <output>.checkpoint.json)."
    )
    parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Disable automatic checkpoint saving."
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging verbosity level."
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Optional path to write log messages to file."
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Do not sleep/wait if rate limit reset threshold is reached; abort instead."
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub personal access token (optional; alternatively set GITHUB_TOKEN environment variable)."
    )
    return parser.parse_args()


def main() -> None:
    """Main execution entry point."""
    args = parse_arguments()
    logger = configure_logging(level_name=args.log_level, log_file=args.log_file)

    logger.info("Initializing GitHub Public Repositories Fetcher...")

    # Determine output format
    fmt = args.format
    if not fmt:
        if args.output.lower().endswith(".csv"):
            fmt = "csv"
        else:
            fmt = "json"

    # Checkpoint configuration
    checkpoint_path = args.checkpoint_file or f"{args.output}.checkpoint.json"
    checkpoint_mgr = CheckpointManager(checkpoint_path)

    start_page = 1
    start_url: Optional[str] = None
    resuming = False
    prior_fetched_count = 0

    if args.resume and checkpoint_mgr.exists():
        cp_data = checkpoint_mgr.load()
        if cp_data and cp_data.get("org") == args.org:
            resuming = True
            start_page = cp_data.get("page_num", 1) + 1
            start_url = cp_data.get("next_url")
            prior_fetched_count = cp_data.get("total_fetched", 0)
            logger.info(
                "Resuming from checkpoint at page %d (%d repositories already saved)...",
                start_page, prior_fetched_count
            )
            if not start_url:
                logger.info("Checkpoint indicates pagination was already completed.")
                return

    # Streaming writer ensures O(1) memory footprint
    writer = StreamingWriter(args.output, fmt, resume=resuming, existing_count=prior_fetched_count)

    # Lightweight streaming statistics
    languages: Counter = Counter()
    total_stars = 0
    total_forks = 0
    top_starred: List[Dict[str, Any]] = []

    start_time = time.time()
    total_fetched = prior_fetched_count
    pagination_completed = False

    def on_page_completed(page_num: int, next_url: Optional[str], items: List[Dict[str, Any]]) -> None:
        """Callback fired whenever a page finishes and is written to disk."""
        if not args.no_checkpoint:
            checkpoint_mgr.save(
                org=args.org,
                page_num=page_num,
                next_url=next_url,
                total_fetched=total_fetched,
                output_file=args.output,
                fmt=fmt
            )

    try:
        with GitHubClient(
            token=args.token,
            wait_on_rate_limit=not args.no_wait,
            request_delay=args.request_delay,
        ) as client:

            repo_stream = client.get_organization_repositories(
                org=args.org,
                per_page=args.per_page,
                max_repos=args.limit,
                start_page=start_page,
                start_url=start_url,
                request_delay=args.request_delay,
                page_callback=on_page_completed
            )

            for raw_repo in repo_stream:
                metadata = extract_repository_metadata(raw_repo)
                writer.write_item(metadata)
                total_fetched += 1

                # Update running statistics
                lang = metadata.get("language") or "Unspecified"
                languages[lang] += 1
                stars = metadata.get("stargazers_count", 0)
                forks = metadata.get("forks_count", 0)
                total_stars += stars
                total_forks += forks

                # Track top 5 starred repos
                top_starred.append({"name": metadata["name"], "stargazers_count": stars})
                top_starred.sort(key=lambda r: r["stargazers_count"], reverse=True)
                if len(top_starred) > 5:
                    top_starred.pop()

                if total_fetched % 100 == 0:
                    logger.info("Progress: streamed %d repositories to disk...", total_fetched)

            pagination_completed = True

    except GitHubRateLimitExceeded as e:
        logger.error("Execution halted due to rate limit: %s", e)
        if total_fetched == 0:
            writer.close()
            sys.exit(1)
        logger.warning("Flushed %d repositories retrieved prior to rate limit halt. Checkpoint preserved.", total_fetched)
    except GitHubAPIError as e:
        logger.error("API error encountered: %s", e)
        if total_fetched == 0:
            writer.close()
            sys.exit(1)
        logger.warning("Flushed %d repositories retrieved prior to error. Checkpoint preserved.", total_fetched)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user (Ctrl+C). Safely flushing retrieved records and preserving checkpoint...")
    except Exception as e:
        logger.exception("Unexpected error occurred: %s", e)
        if total_fetched == 0:
            writer.close()
            sys.exit(1)
    finally:
        writer.close()
        # Clean up checkpoint file only if pagination finished naturally
        if pagination_completed and not args.no_checkpoint:
            checkpoint_mgr.remove()

    elapsed_time = time.time() - start_time
    logger.info("Retrieved and wrote %d total repositories in %.2f seconds.", total_fetched, elapsed_time)
    logger.info("Successfully wrote output to '%s' (format: %s).", args.output, fmt)

    # Print summary table
    print_summary(
        total_count=total_fetched,
        total_stars=total_stars,
        total_forks=total_forks,
        languages=languages,
        top_starred=top_starred,
        org=args.org,
        elapsed_time=elapsed_time
    )


if __name__ == "__main__":
    main()
