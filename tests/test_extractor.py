"""Unit tests for repository metadata extractor."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from repo_pipeline.extractor import extract_batch, extract_repository_metadata


class TestExtractor(unittest.TestCase):

    def test_extract_full_metadata(self):
        raw = {
            "id": 123456,
            "name": "vscode",
            "full_name": "microsoft/vscode",
            "description": "Visual Studio Code",
            "language": "TypeScript",
            "html_url": "https://github.com/microsoft/vscode",
            "clone_url": "https://github.com/microsoft/vscode.git",
            "homepage": "https://code.visualstudio.com",
            "default_branch": "main",
            "stargazers_count": 160000,
            "forks_count": 30000,
            "watchers_count": 160000,
            "open_issues_count": 5000,
            "size": 450000,
            "fork": False,
            "archived": False,
            "disabled": False,
            "license": {
                "key": "mit",
                "name": "MIT License",
                "spdx_id": "MIT"
            },
            "topics": ["editor", "electron", "typescript"],
            "created_at": "2015-09-03T20:23:38Z",
            "updated_at": "2026-11-20T10:00:00Z",
            "pushed_at": "2026-11-20T12:00:00Z",
            "visibility": "public"
        }

        extracted = extract_repository_metadata(raw)
        self.assertEqual(extracted["id"], 123456)
        self.assertEqual(extracted["name"], "vscode")
        self.assertEqual(extracted["full_name"], "microsoft/vscode")
        self.assertEqual(extracted["description"], "Visual Studio Code")
        self.assertEqual(extracted["language"], "TypeScript")
        self.assertEqual(extracted["html_url"], "https://github.com/microsoft/vscode")
        self.assertEqual(extracted["clone_url"], "https://github.com/microsoft/vscode.git")
        self.assertEqual(extracted["homepage"], "https://code.visualstudio.com")
        self.assertEqual(extracted["default_branch"], "main")
        self.assertEqual(extracted["stargazers_count"], 160000)
        self.assertEqual(extracted["forks_count"], 30000)
        self.assertEqual(extracted["is_fork"], False)
        self.assertEqual(extracted["is_archived"], False)
        self.assertEqual(extracted["license_name"], "MIT License")
        self.assertEqual(extracted["license_spdx_id"], "MIT")
        self.assertEqual(extracted["topics"], ["editor", "electron", "typescript"])
        self.assertEqual(extracted["visibility"], "public")

    def test_extract_minimal_metadata(self):
        raw = {
            "id": 999,
            "name": "minimal-repo",
            "full_name": "microsoft/minimal-repo",
        }
        extracted = extract_repository_metadata(raw)
        self.assertEqual(extracted["id"], 999)
        self.assertEqual(extracted["name"], "minimal-repo")
        self.assertIsNone(extracted["description"])
        self.assertIsNone(extracted["language"])
        self.assertIsNone(extracted["license_name"])
        self.assertIsNone(extracted["license_spdx_id"])
        self.assertEqual(extracted["stargazers_count"], 0)
        self.assertEqual(extracted["forks_count"], 0)
        self.assertEqual(extracted["topics"], [])
        self.assertEqual(extracted["is_fork"], False)

    def test_extract_batch(self):
        repos = [
            {"id": 1, "name": "r1"},
            {"id": 2, "name": "r2"}
        ]
        batch = extract_batch(repos)
        self.assertEqual(len(batch), 2)
        self.assertEqual(batch[0]["name"], "r1")
        self.assertEqual(batch[1]["name"], "r2")


if __name__ == "__main__":
    unittest.main()
