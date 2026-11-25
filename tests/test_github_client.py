"""Unit tests for GitHubClient class, pagination, request delays, and checkpointing."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from repo_pipeline.github_client import (
    GitHubAPIError,
    GitHubClient,
    GitHubRateLimitExceeded,
    parse_retry_after,
)
from repo_pipeline.cli.scrape import CheckpointManager, StreamingWriter


class TestRetryAfterParser(unittest.TestCase):

    def test_parse_integer_seconds(self):
        self.assertEqual(parse_retry_after("120"), 120.0)
        self.assertEqual(parse_retry_after(" 0 "), 0.0)

    def test_parse_http_date(self):
        # Using a date guaranteed to be in the past
        past_date = "Sun, 06 Nov 1994 08:49:37 GMT"
        self.assertEqual(parse_retry_after(past_date), 0.0)

    def test_parse_invalid(self):
        self.assertEqual(parse_retry_after("invalid-format"), 60.0)


class TestGitHubClient(unittest.TestCase):

    def setUp(self):
        self.env_patcher = patch.dict(os.environ, {}, clear=True)
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    def test_initialization_unauthenticated(self):
        client = GitHubClient(token=None)
        self.assertFalse(client.is_authenticated)
        self.assertNotIn("Authorization", client.session.headers)
        self.assertEqual(client.session.headers["Accept"], "application/vnd.github+json")
        self.assertEqual(client.session.headers["X-GitHub-Api-Version"], "2022-11-28")
        self.assertEqual(client.request_delay, 0.0)

    def test_initialization_authenticated_param(self):
        client = GitHubClient(token="ghp_testtoken123456", request_delay=0.5)
        self.assertTrue(client.is_authenticated)
        self.assertEqual(client.session.headers["Authorization"], "Bearer ghp_testtoken123456")
        self.assertEqual(client.request_delay, 0.5)

    def test_initialization_authenticated_env(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_envtoken9999"}):
            client = GitHubClient()
            self.assertTrue(client.is_authenticated)
            self.assertEqual(client.session.headers["Authorization"], "Bearer ghp_envtoken9999")

    @patch("requests.Session.get")
    def test_get_success(self, mock_get):
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.headers = {
            "x-ratelimit-limit": "5000",
            "x-ratelimit-remaining": "4999",
            "x-ratelimit-reset": "1700000000"
        }
        mock_get.return_value = mock_response

        client = GitHubClient(token="test")
        resp = client.get("https://api.github.com/orgs/microsoft/repos")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_get.call_count, 1)

    @patch("requests.Session.get")
    def test_get_404_raises_api_error_immediately(self, mock_get):
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 404
        mock_response.text = '{"message": "Not Found"}'
        mock_response.headers = {}
        mock_get.return_value = mock_response

        client = GitHubClient(max_retries=3)
        with self.assertRaises(GitHubAPIError) as ctx:
            client.get("https://api.github.com/orgs/nonexistent_org/repos")

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(mock_get.call_count, 1)

    @patch("time.sleep")
    @patch("requests.Session.get")
    def test_get_500_server_error_retries_and_succeeds(self, mock_get, mock_sleep):
        resp_500 = MagicMock(spec=requests.Response)
        resp_500.status_code = 500
        resp_500.text = "Internal Server Error"
        resp_500.headers = {}

        resp_200 = MagicMock(spec=requests.Response)
        resp_200.status_code = 200
        resp_200.headers = {"x-ratelimit-remaining": "50"}

        mock_get.side_effect = [resp_500, resp_200]

        client = GitHubClient(max_retries=3, backoff_factor=0.01)
        resp = client.get("https://api.github.com/test")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_once()

    @patch("time.sleep")
    @patch("requests.Session.get")
    def test_get_429_retry_after(self, mock_get, mock_sleep):
        resp_429 = MagicMock(spec=requests.Response)
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "5", "x-ratelimit-remaining": "0"}

        resp_200 = MagicMock(spec=requests.Response)
        resp_200.status_code = 200
        resp_200.headers = {"x-ratelimit-remaining": "40"}

        mock_get.side_effect = [resp_429, resp_200]

        client = GitHubClient(wait_on_rate_limit=True)
        resp = client.get("https://api.github.com/test")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_with(5.0)

    @patch("requests.Session.get")
    def test_get_429_no_wait_raises(self, mock_get):
        resp_429 = MagicMock(spec=requests.Response)
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "10"}
        mock_get.return_value = resp_429

        client = GitHubClient(wait_on_rate_limit=False)
        with self.assertRaises(GitHubRateLimitExceeded):
            client.get("https://api.github.com/test")

    @patch("time.sleep")
    @patch("requests.Session.get")
    def test_pagination_follows_link_headers(self, mock_get, mock_sleep):
        page1_resp = MagicMock(spec=requests.Response)
        page1_resp.status_code = 200
        page1_resp.headers = {"x-ratelimit-remaining": "50"}
        page1_resp.links = {"next": {"url": "https://api.github.com/orgs/microsoft/repos?page=2"}}
        page1_resp.json.return_value = [{"id": 1, "name": "repo1"}]

        page2_resp = MagicMock(spec=requests.Response)
        page2_resp.status_code = 200
        page2_resp.headers = {"x-ratelimit-remaining": "49"}
        page2_resp.links = {}  # No next page
        page2_resp.json.return_value = [{"id": 2, "name": "repo2"}]

        mock_get.side_effect = [page1_resp, page2_resp]

        client = GitHubClient()
        repos = list(client.get_organization_repositories("microsoft"))

        self.assertEqual(len(repos), 2)
        self.assertEqual(repos[0]["name"], "repo1")
        self.assertEqual(repos[1]["name"], "repo2")
        self.assertEqual(mock_get.call_count, 2)

    @patch("time.sleep")
    @patch("requests.Session.get")
    def test_pagination_with_configurable_request_delay(self, mock_get, mock_sleep):
        page1_resp = MagicMock(spec=requests.Response)
        page1_resp.status_code = 200
        page1_resp.headers = {"x-ratelimit-remaining": "50"}
        page1_resp.links = {"next": {"url": "https://api.github.com/orgs/microsoft/repos?page=2"}}
        page1_resp.json.return_value = [{"id": 1, "name": "repo1"}]

        page2_resp = MagicMock(spec=requests.Response)
        page2_resp.status_code = 200
        page2_resp.headers = {"x-ratelimit-remaining": "49"}
        page2_resp.links = {}
        page2_resp.json.return_value = [{"id": 2, "name": "repo2"}]

        mock_get.side_effect = [page1_resp, page2_resp]

        client = GitHubClient(request_delay=0.75)
        repos = list(client.get_organization_repositories("microsoft"))

        self.assertEqual(len(repos), 2)
        mock_sleep.assert_called_with(0.75)

    @patch("requests.Session.get")
    def test_pagination_page_callback(self, mock_get):
        page1_resp = MagicMock(spec=requests.Response)
        page1_resp.status_code = 200
        page1_resp.headers = {}
        page1_resp.links = {"next": {"url": "https://api.github.com/orgs/microsoft/repos?page=2"}}
        page1_resp.json.return_value = [{"id": 1, "name": "repo1"}]

        page2_resp = MagicMock(spec=requests.Response)
        page2_resp.status_code = 200
        page2_resp.headers = {}
        page2_resp.links = {}
        page2_resp.json.return_value = [{"id": 2, "name": "repo2"}]

        mock_get.side_effect = [page1_resp, page2_resp]

        callback_calls = []

        def callback(page_num, next_url, items):
            callback_calls.append((page_num, next_url, len(items)))

        client = GitHubClient()
        list(client.get_organization_repositories("microsoft", page_callback=callback))

        self.assertEqual(len(callback_calls), 2)
        self.assertEqual(callback_calls[0][0], 1)
        self.assertEqual(callback_calls[0][1], "https://api.github.com/orgs/microsoft/repos?page=2")
        self.assertEqual(callback_calls[1][0], 2)
        self.assertIsNone(callback_calls[1][1])

    @patch("requests.Session.get")
    def test_pagination_respects_max_repos_limit(self, mock_get):
        page1_resp = MagicMock(spec=requests.Response)
        page1_resp.status_code = 200
        page1_resp.headers = {"x-ratelimit-remaining": "50"}
        page1_resp.links = {"next": {"url": "https://api.github.com/orgs/microsoft/repos?page=2"}}
        page1_resp.json.return_value = [{"id": 1, "name": "r1"}, {"id": 2, "name": "r2"}, {"id": 3, "name": "r3"}]

        mock_get.return_value = page1_resp

        client = GitHubClient()
        repos = list(client.get_organization_repositories("microsoft", max_repos=2))

        self.assertEqual(len(repos), 2)
        self.assertEqual(repos[0]["name"], "r1")
        self.assertEqual(repos[1]["name"], "r2")
        self.assertEqual(mock_get.call_count, 1)


class TestCheckpointAndResume(unittest.TestCase):

    def test_checkpoint_manager_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cp_path = os.path.join(tmp_dir, "test.checkpoint.json")
            mgr = CheckpointManager(cp_path)
            self.assertFalse(mgr.exists())
            self.assertIsNone(mgr.load())

            # Save state
            mgr.save(
                org="microsoft",
                page_num=3,
                next_url="https://api.github.com/orgs/microsoft/repos?page=4",
                total_fetched=300,
                output_file="out.json",
                fmt="json"
            )
            self.assertTrue(mgr.exists())

            loaded = mgr.load()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["org"], "microsoft")
            self.assertEqual(loaded["page_num"], 3)
            self.assertEqual(loaded["total_fetched"], 300)

            # Remove state
            mgr.remove()
            self.assertFalse(mgr.exists())

    def test_streaming_writer_resume_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            json_file = os.path.join(tmp_dir, "test.json")

            # Phase 1: write initial batch
            writer1 = StreamingWriter(json_file, fmt="json")
            writer1.write_item({"id": 1, "name": "repo1"})
            writer1.write_item({"id": 2, "name": "repo2"})
            writer1.close()

            # Verify initial file
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(len(data), 2)

            # Phase 2: resume and append batch
            writer2 = StreamingWriter(json_file, fmt="json", resume=True, existing_count=2)
            writer2.write_item({"id": 3, "name": "repo3"})
            writer2.close()

            # Verify resumed file is valid JSON with 3 items
            with open(json_file, "r", encoding="utf-8") as f:
                data_after = json.load(f)
            self.assertEqual(len(data_after), 3)
            self.assertEqual([d["id"] for d in data_after], [1, 2, 3])

    def test_streaming_writer_resume_csv(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = os.path.join(tmp_dir, "test.csv")

            # Phase 1: initial batch
            writer1 = StreamingWriter(csv_file, fmt="csv")
            writer1.write_item({"id": 1, "name": "repo1"})
            writer1.close()

            # Phase 2: resume and append
            writer2 = StreamingWriter(csv_file, fmt="csv", resume=True, existing_count=1)
            writer2.write_item({"id": 2, "name": "repo2"})
            writer2.close()

            with open(csv_file, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]

            # Header + 2 data rows = 3 lines (no duplicate header)
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[0], "id,name")
            self.assertEqual(lines[1], "1,repo1")
            self.assertEqual(lines[2], "2,repo2")


if __name__ == "__main__":
    unittest.main()
