"""
Unit tests for Validator, FieldRule, ValidationConfig, and ValidationReport.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from repo_pipeline.sample_data import generate_corrupted_sample_dataset, get_github_repository_validation_config
from repo_pipeline.validator import (
    FieldRule,
    Severity,
    ValidationChecks,
    ValidationConfig,
    ValidationIssue,
    ValidationReport,
    Validator,
    is_valid_url,
)


class TestValidator(unittest.TestCase):

    def setUp(self):
        self.config = ValidationConfig(primary_key="id")
        self.config.add_field_rule(FieldRule(name="id", required=True, expected_type=int))
        self.config.add_field_rule(FieldRule(name="name", required=True, expected_type=str, allow_empty_string=False))
        self.config.add_field_rule(FieldRule(name="url", required=True, expected_type=str, is_url=True))
        self.config.add_field_rule(FieldRule(name="stars", required=True, expected_type=int, min_value=0))
        self.config.add_field_rule(FieldRule(name="description", required=False, expected_type=str, allow_none=True))
        self.validator = Validator(self.config)

    def test_valid_record(self):
        record = {
            "id": 1,
            "name": "vscode",
            "url": "https://github.com/microsoft/vscode",
            "stars": 150000,
            "description": "Code editor",
        }
        issues = self.validator.validate_record(record, index=0, seen_keys=set())
        errors = [i for i in issues if i.severity == Severity.ERROR]
        self.assertEqual(len(errors), 0)

    def test_preserve_special_characters(self):
        special_str = "C++ & C# 🚀 café (日本語) <tag> 'quote' \"double\""
        record = {
            "id": 2,
            "name": special_str,
            "url": "https://github.com/microsoft/special",
            "stars": 10,
            "description": special_str,
        }
        issues = self.validator.validate_record(record, index=0, seen_keys=set())
        errors = [i for i in issues if i.severity == Severity.ERROR]
        self.assertEqual(len(errors), 0)
        self.assertEqual(record["name"], special_str)
        self.assertEqual(record["description"], special_str)

    def test_missing_required_vs_optional_fields(self):
        record = {
            "id": 3,
            "url": "https://github.com/microsoft/test",
            "stars": 5,
        }
        issues = self.validator.validate_record(record, index=0, seen_keys=set())
        errors = [i for i in issues if i.severity == Severity.ERROR]
        infos = [i for i in issues if i.severity == Severity.INFO]

        name_issues = [i for i in errors if i.field == "name" and i.code == "MISSING_REQUIRED_FIELD"]
        self.assertEqual(len(name_issues), 1)

        desc_issues = [i for i in infos if i.field == "description" and i.code == "MISSING_OPTIONAL_DATA"]
        self.assertEqual(len(desc_issues), 1)

    def test_detect_duplicate_primary_keys(self):
        records = [
            {"id": 10, "name": "repo1", "url": "https://github.com/repo1", "stars": 1},
            {"id": 10, "name": "repo2", "url": "https://github.com/repo2", "stars": 2},
        ]
        report = self.validator.validate(records)
        self.assertFalse(report.is_valid)
        dup_issues = [i for i in report.errors if i.code == "DUPLICATE_PRIMARY_KEY"]
        self.assertEqual(len(dup_issues), 1)
        self.assertEqual(dup_issues[0].record_id, 10)

    def test_data_type_mismatch(self):
        record = {
            "id": 4,
            "name": "repo",
            "url": "https://github.com/repo",
            "stars": "one-thousand",
        }
        issues = self.validator.validate_record(record, index=0, seen_keys=set())
        type_errors = [i for i in issues if i.code == "TYPE_MISMATCH"]
        self.assertEqual(len(type_errors), 1)
        self.assertEqual(type_errors[0].field, "stars")

    def test_url_format_validation(self):
        self.assertTrue(is_valid_url("https://github.com/microsoft/vscode"))
        self.assertTrue(is_valid_url("http://example.com:8080/path?query=1#hash"))
        self.assertFalse(is_valid_url("ftp://github.com/not-http"))
        self.assertFalse(is_valid_url("not a url"))
        self.assertFalse(is_valid_url("http://"))
        self.assertFalse(is_valid_url(""))
        self.assertFalse(is_valid_url(None))

        record = {
            "id": 5,
            "name": "bad-url",
            "url": "httppps://malformed",
            "stars": 0,
        }
        issues = self.validator.validate_record(record, index=0, seen_keys=set())
        url_errors = [i for i in issues if i.code == "MALFORMED_URL"]
        self.assertEqual(len(url_errors), 1)

    def test_empty_string_validation(self):
        record = {
            "id": 6,
            "name": "   ",
            "url": "https://github.com/repo",
            "stars": 0,
        }
        issues = self.validator.validate_record(record, index=0, seen_keys=set())
        empty_errors = [i for i in issues if i.code == "EMPTY_VALUE"]
        self.assertEqual(len(empty_errors), 1)

    def test_numeric_min_bounds(self):
        record = {
            "id": 7,
            "name": "negative-stars",
            "url": "https://github.com/repo",
            "stars": -10,
        }
        issues = self.validator.validate_record(record, index=0, seen_keys=set())
        min_errors = [i for i in issues if i.code == "VALUE_BELOW_MIN"]
        self.assertEqual(len(min_errors), 1)

    def test_custom_record_validator(self):
        def custom_check(rec, idx):
            if rec.get("stars", 0) > 1000 and "popular" not in rec.get("name", ""):
                return [ValidationIssue(rec.get("id"), "name", "NOT_POPULAR_NAME", "Needs popular in name", Severity.WARNING)]
            return []

        self.config.add_record_validator(custom_check)
        rec = {"id": 8, "name": "normal", "url": "https://github.com/repo", "stars": 5000}
        issues = self.validator.validate_record(rec, index=0, seen_keys=set())
        warns = [i for i in issues if i.code == "NOT_POPULAR_NAME"]
        self.assertEqual(len(warns), 1)

    def test_configurable_checks_toggle(self):
        # Create a record that has multiple errors: duplicate ID, bad URL, and type mismatch
        records = [
            {"id": 20, "name": "r1", "url": "https://valid.com", "stars": 10},
            {"id": 20, "name": "r2", "url": "bad-url-here", "stars": "five"},
        ]

        # 1. With all checks enabled
        config_full = ValidationConfig(primary_key="id")
        config_full.add_field_rule(FieldRule(name="id", required=True, expected_type=int))
        config_full.add_field_rule(FieldRule(name="name", required=True, expected_type=str))
        config_full.add_field_rule(FieldRule(name="url", required=True, expected_type=str, is_url=True))
        config_full.add_field_rule(FieldRule(name="stars", required=True, expected_type=int))
        v_full = Validator(config_full)
        rep_full = v_full.validate(records)
        self.assertEqual(len(rep_full.errors), 3)  # Duplicate ID + Bad URL + Type Mismatch

        # 2. Disable URL check and duplicate check
        config_partial = ValidationConfig(primary_key="id")
        config_partial.add_field_rule(FieldRule(name="id", required=True, expected_type=int))
        config_partial.add_field_rule(FieldRule(name="name", required=True, expected_type=str))
        config_partial.add_field_rule(FieldRule(name="url", required=True, expected_type=str, is_url=True))
        config_partial.add_field_rule(FieldRule(name="stars", required=True, expected_type=int))
        config_partial.disable_check("check_url_formats")
        config_partial.disable_check("check_duplicate_keys")

        v_partial = Validator(config_partial)
        rep_partial = v_partial.validate(records)
        # Should now only report the Type Mismatch error
        self.assertEqual(len(rep_partial.errors), 1)
        self.assertEqual(rep_partial.errors[0].code, "TYPE_MISMATCH")

    def test_generate_three_reports_md_pdf_json(self):
        records = [
            {"id": 1, "name": "vscode", "url": "https://code.visualstudio.com", "stars": 150000},
            {"id": 2, "name": "broken", "url": "not-url", "stars": -5},
        ]
        report = self.validator.validate(records)

        with tempfile.TemporaryDirectory() as tmp_dir:
            base = os.path.join(tmp_dir, "test_rep")
            paths = report.generate_reports(base_name=base)

            md_path = paths["markdown"]
            pdf_path = paths["pdf"]
            json_path = paths["json"]

            # Verify files exist
            self.assertTrue(os.path.exists(md_path))
            self.assertTrue(os.path.exists(json_path))

            # Verify Markdown file content
            with open(md_path, "r", encoding="utf-8") as f:
                md_content = f.read()
            self.assertIn("# Data Validation Report", md_content)
            self.assertIn("MALFORMED_URL", md_content)

            # Verify PDF file if Playwright is installed (optional dependency: pip install .[pdf])
            try:
                import playwright
                has_playwright = True
            except ImportError:
                has_playwright = False

            if has_playwright:
                self.assertTrue(os.path.exists(pdf_path))
                self.assertGreater(os.path.getsize(pdf_path), 1000)

            # Verify JSON file structure
            with open(json_path, "r", encoding="utf-8") as f:
                json_data = json.load(f)
            self.assertIn("summary", json_data)
            self.assertIn("issues", json_data)
            self.assertEqual(json_data["summary"]["total_records"], 2)

    def test_change_field_requirement_status(self):
        # By default in setUp: 'name' is required, 'description' is optional
        rec = {"id": 100, "url": "https://example.com", "stars": 5}
        # rec is missing both 'name' and 'description'
        issues1 = self.validator.validate_record(rec, index=0, seen_keys=set())
        # name missing -> ERROR; description missing -> INFO
        self.assertTrue(any(i.field == "name" and i.severity == Severity.ERROR for i in issues1))
        self.assertTrue(any(i.field == "description" and i.severity == Severity.INFO for i in issues1))

        # Now change 'description' to required and 'name' to optional
        self.config.make_required("description")
        self.config.make_optional("name")

        issues2 = self.validator.validate_record(rec, index=0, seen_keys=set())
        # name missing -> INFO; description missing -> ERROR
        self.assertTrue(any(i.field == "name" and i.severity == Severity.INFO for i in issues2))
        self.assertTrue(any(i.field == "description" and i.severity == Severity.ERROR for i in issues2))

    def test_corrupted_sample_dataset_coverage(self):
        full_config = get_github_repository_validation_config()
        validator = Validator(full_config)
        dataset = generate_corrupted_sample_dataset()
        report = validator.validate(dataset)

        self.assertEqual(report.total_records, 11)
        self.assertFalse(report.is_valid)
        self.assertGreater(len(report.errors), 0)
        self.assertGreater(len(report.infos), 0)

        # Check markdown generation
        md_rep = report.generate_markdown_report()
        self.assertIn("Data Validation Report", md_rep)

        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            report.save_json(tmp_path)
            with open(tmp_path, "r", encoding="utf-8") as f:
                saved_data = json.load(f)
            self.assertIn("summary", saved_data)
            self.assertIn("issues", saved_data)
            self.assertEqual(saved_data["summary"]["total_records"], 11)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()
