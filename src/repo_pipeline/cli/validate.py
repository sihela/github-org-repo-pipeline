#!/usr/bin/env python3
"""
CLI Tool for Data Validation, Report Generation, and Optional Cleaning.

Produces exactly three reports from the validator:
1. One Markdown file (.md)
2. One PDF file (.pdf)
3. One JSON file (.json) formatted for downstream cleaner consumption

Validation checks are fully configurable with default checks enabled.
The validator is fully independent and has zero coupling to the cleaner.
"""

import argparse
import csv
import json
import logging
import sys
from typing import Any, Dict, List

from repo_pipeline.sample_data import generate_corrupted_sample_dataset, get_github_repository_validation_config
from repo_pipeline.validator import ValidationConfig, ValidationReport, Validator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("validate_cli")


def load_dataset(filepath: str) -> List[Dict[str, Any]]:
    """Load records from either a JSON or CSV file."""
    if filepath.lower().endswith(".csv"):
        records: List[Dict[str, Any]] = []
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                converted: Dict[str, Any] = {}
                for k, v in row.items():
                    if v == "":
                        converted[k] = None
                    elif v in ("True", "true"):
                        converted[k] = True
                    elif v in ("False", "false"):
                        converted[k] = False
                    elif k.endswith("_count") or k in ("id", "size_kb", "open_issues_count"):
                        try:
                            converted[k] = int(v)
                        except ValueError:
                            converted[k] = v
                    elif k == "topics" and v:
                        converted[k] = [t.strip() for t in v.split(";")]
                    else:
                        converted[k] = v
                records.append(converted)
        return records
    else:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("Expected JSON array of records.")
            return data


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Validate datasets with configurable checks and generate MD, PDF, and JSON reports.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--input",
        "-i",
        default=None,
        help="Path to input dataset (.json or .csv). If omitted, tests with corrupted sample dataset."
    )
    parser.add_argument(
        "--generate-sample",
        default=None,
        help="Save the deliberately corrupted sample dataset to this JSON path and exit."
    )
    parser.add_argument(
        "--report-base",
        default="validation_report",
        help="Base filename for generated reports (generates .md, .pdf, and .json)."
    )
    parser.add_argument(
        "--report-md",
        default=None,
        help="Explicit path for the single Markdown report (defaults to <report-base>.md)."
    )
    parser.add_argument(
        "--report-pdf",
        default=None,
        help="Explicit path for the single PDF report (defaults to <report-base>.pdf)."
    )
    parser.add_argument(
        "--report-json",
        default=None,
        help="Explicit path for the single JSON report (defaults to <report-base>.json)."
    )
    parser.add_argument(
        "--disable-check",
        action="append",
        default=[],
        help="Disable a specific check flag (e.g., --disable-check check_url_formats)."
    )
    parser.add_argument(
        "--enable-check",
        action="append",
        default=[],
        help="Enable a specific check flag (e.g., --enable-check check_unknown_fields)."
    )
    parser.add_argument(
        "--require-field",
        action="append",
        default=[],
        help="Change a field from optional to required (e.g., --require-field description)."
    )
    parser.add_argument(
        "--optional-field",
        action="append",
        default=[],
        help="Change a field from required to optional (e.g., --optional-field default_branch)."
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Optional post-validation cleaning pass using cleaner.py (separate step)."
    )
    parser.add_argument(
        "--output-clean",
        default="cleaned_records.json",
        help="Destination path for cleaned records if --clean is enabled."
    )
    parser.add_argument(
        "--output-quarantine",
        default="quarantined_records.json",
        help="Destination path for quarantined records if --clean is enabled."
    )

    return parser.parse_args()


def main() -> None:
    """Main CLI execution flow."""
    args = parse_args()

    # Mode 1: Generate Corrupted Sample
    if args.generate_sample:
        from repo_pipeline.cleaner import export_dataset_json
        sample_records = generate_corrupted_sample_dataset()
        export_dataset_json(sample_records, args.generate_sample)
        print(f"Deliberately corrupted sample dataset saved to: {args.generate_sample} ({len(sample_records)} records)")
        return

    # Load records (either from user input or generate corrupted sample dataset)
    if args.input:
        logger.info("Loading input dataset from: %s", args.input)
        records = load_dataset(args.input)
    else:
        logger.info("No input specified. Loading built-in corrupted sample dataset...")
        records = generate_corrupted_sample_dataset()

    logger.info("Total records loaded: %d", len(records))

    # Configure validation rules and checks
    config = get_github_repository_validation_config()

    # Apply check configuration overrides
    for chk in args.disable_check:
        config.disable_check(chk)
        logger.info("Disabled validation check: %s", chk)

    for chk in args.enable_check:
        config.enable_check(chk)
        logger.info("Enabled validation check: %s", chk)

    for fld in args.require_field:
        config.make_required(fld)
        logger.info("Configured field '%s' as REQUIRED", fld)

    for fld in args.optional_field:
        config.make_optional(fld)
        logger.info("Configured field '%s' as OPTIONAL", fld)

    # Execute validation (Validator is 100% independent)
    logger.info("Executing independent validation checks...")
    validator = Validator(config)
    report: ValidationReport = validator.validate(records)

    # Determine paths for the three report files
    md_file = args.report_md or f"{args.report_base}.md"
    pdf_file = args.report_pdf or f"{args.report_base}.pdf"
    json_file = args.report_json or f"{args.report_base}.json"

    # Generate the exactly 3 report artifacts
    logger.info("Generating validation reports: 1 Markdown (.md), 1 PDF (.pdf), 1 JSON (.json)...")
    report_paths = report.generate_reports(
        base_name=args.report_base,
        md_path=md_file,
        pdf_path=pdf_file,
        json_path=json_file
    )

    print("\n" + "=" * 70)
    print(" VALIDATION COMPLETED - GENERATED REPORTS:")
    print("=" * 70)
    print(f"  1. Markdown Report:  {report_paths['markdown']}")
    print(f"  2. PDF Report:       {report_paths['pdf']}")
    print(f"  3. JSON Report:      {report_paths['json']}")
    print("=" * 70)
    summary = report.get_summary_dict()
    print(f"Total: {summary['total_records']:,} | Valid: {summary['valid_records']:,} | Errors: {summary['invalid_records']:,} | Warnings: {summary['records_with_warnings']:,} | Info: {summary['records_with_info']:,}")
    print("=" * 70 + "\n")

    # Optional Downstream Cleaning (Completely separate from Validator)
    if args.clean:
        logger.info("Triggering optional downstream cleaner using generated JSON report: %s", json_file)
        from repo_pipeline.cleaner import DataCleaner, export_dataset_csv, export_dataset_json
        cleaner = DataCleaner(config=config)
        cleaned_recs, quar_recs, clean_rep = cleaner.clean(records, validation_report=json_file)

        print("\n" + clean_rep.generate_text_report() + "\n")

        if args.output_clean.lower().endswith(".csv"):
            export_dataset_csv(cleaned_recs, args.output_clean)
        else:
            export_dataset_json(cleaned_recs, args.output_clean)
        logger.info("Exported %d clean records to: %s", len(cleaned_recs), args.output_clean)

        if args.output_quarantine.lower().endswith(".csv"):
            export_dataset_csv(quar_recs, args.output_quarantine)
        else:
            export_dataset_json(quar_recs, args.output_quarantine)
        logger.info("Exported %d quarantined records to: %s", len(quar_recs), args.output_quarantine)


if __name__ == "__main__":
    main()
