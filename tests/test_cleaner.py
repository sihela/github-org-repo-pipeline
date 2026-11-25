"""
Unit tests for DataCleaner and CleaningReport.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from repo_pipeline.cleaner import CleaningReport, DataCleaner, export_dataset_csv, export_dataset_json
from repo_pipeline.sample_data import generate_corrupted_sample_dataset, get_github_repository_validation_config
from repo_pipeline.validator import FieldRule, ValidationConfig, Validator


class TestDataCleaner(unittest.TestCase):

    def setUp(self):
        self.config = ValidationConfig(primary_key="id")
        self.config.add_field_rule(FieldRule(name="id", required=True, expected_type=int))
        self.config.add_field_rule(FieldRule(name="name", required=True, expected_type=str, allow_empty_string=False))
        self.config.add_field_rule(FieldRule(name="stars", required=True, expected_type=int, min_value=0))
        self.config.add_field_rule(FieldRule(name="url", required=True, expected_type=str, is_url=True))
        self.config.add_field_rule(FieldRule(name="description", required=False, expected_type=str, allow_none=True))
        self.cleaner = DataCleaner(self.config)

    def test_deduplication_keep_first(self):
        records = [
            {"id": 1, "name": "first", "stars": 10, "url": "https://example.com/1"},
            {"id": 1, "name": "second", "stars": 20, "url": "https://example.com/2"},
        ]
        clean_recs, quar_recs, rep = self.cleaner.clean(records)
        self.assertEqual(len(clean_recs), 1)
        self.assertEqual(clean_recs[0]["name"], "first")
        self.assertEqual(len(quar_recs), 1)
        self.assertEqual(rep.duplicates_removed_count, 1)

    def test_deduplication_keep_last(self):
        cleaner_last = DataCleaner(self.config, duplicate_policy="keep_last")
        records = [
            {"id": 1, "name": "first", "stars": 10, "url": "https://example.com/1"},
            {"id": 1, "name": "second", "stars": 20, "url": "https://example.com/2"},
        ]
        clean_recs, quar_recs, rep = cleaner_last.clean(records)
        self.assertEqual(len(clean_recs), 1)
        self.assertEqual(clean_recs[0]["name"], "second")
        self.assertEqual(len(quar_recs), 1)
        self.assertEqual(rep.duplicates_removed_count, 1)

    def test_field_repairs_and_special_character_preservation(self):
        special_name = "  C++ & C# 🚀 café (日本語)  "
        expected_cleaned_name = "C++ & C# 🚀 café (日本語)"
        record = {
            "id": 2,
            "name": special_name,
            "stars": "500",  # String number to be coerced
            "url": "https://example.com",
            "description": "",  # Empty optional string to be normalized to None
        }
        clean_recs, quar_recs, rep = self.cleaner.clean([record])
        self.assertEqual(len(clean_recs), 1)
        self.assertEqual(clean_recs[0]["name"], expected_cleaned_name)
        self.assertEqual(clean_recs[0]["stars"], 500)
        self.assertIsNone(clean_recs[0]["description"])
        self.assertGreater(rep.fields_repaired_count, 0)

    def test_quarantine_unrecoverable_error(self):
        records = [
            # Fatal error: missing required 'name'
            {"id": 3, "stars": 10, "url": "https://example.com"},
            # Fatal error: invalid URL
            {"id": 4, "name": "good", "stars": 10, "url": "not-a-valid-url"},
            # Valid record
            {"id": 5, "name": "valid", "stars": 10, "url": "https://example.com"},
        ]
        clean_recs, quar_recs, rep = self.cleaner.clean(records)
        self.assertEqual(len(clean_recs), 1)
        self.assertEqual(clean_recs[0]["id"], 5)
        self.assertEqual(len(quar_recs), 2)
        self.assertEqual(rep.quarantined_records_count, 2)

    def test_clean_using_json_report_file(self):
        dataset = generate_corrupted_sample_dataset()
        full_config = get_github_repository_validation_config()
        validator = Validator(full_config)
        report = validator.validate(dataset)

        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            report.save_json(tmp_path)
            cleaner = DataCleaner(full_config)
            clean_recs, quar_recs, rep = cleaner.clean(dataset, validation_report=tmp_path)

            self.assertEqual(rep.total_input_records, 11)
            self.assertEqual(len(clean_recs), 4)  # 3 original valid + 1 repaired (Record 10)
            self.assertEqual(len(quar_recs), 7)
            self.assertIn("DATA CLEANING REPORT", rep.generate_text_report())
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_export_json_and_csv_preserves_unicode(self):
        data = [
            {"id": 1, "name": "日本語 café ⚡", "topics": ["tag1", "tag2"]},
        ]
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tmp_json:
            json_path = tmp_json.name
        with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as tmp_csv:
            csv_path = tmp_csv.name

        try:
            export_dataset_json(data, json_path)
            export_dataset_csv(data, csv_path)

            with open(json_path, "r", encoding="utf-8") as f:
                loaded_json = json.load(f)
            self.assertEqual(loaded_json[0]["name"], "日本語 café ⚡")

            with open(csv_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("日本語 café ⚡", content)
            self.assertIn("tag1; tag2", content)
        finally:
            if os.path.exists(json_path):
                os.remove(json_path)
            if os.path.exists(csv_path):
                os.remove(csv_path)


if __name__ == "__main__":
    unittest.main()
