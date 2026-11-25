"""
Official GitHub REST API Client.

A robust, read-only client for interacting with the GitHub REST API.
Handles pagination, rate limits (including HTTP 429 and Retry-After),
exponential backoff, configurable request delays, checkpoint callbacks,
and optional token authentication via environment variables.
"""

import email.utils
import logging
import os
import random
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

import requests

logger = logging.getLogger("github_client")


class GitHubError(Exception):
    """Base exception for all GitHub client errors."""
    pass


class GitHubAPIError(GitHubError):
    """Exception raised for unrecoverable API errors (e.g., 404, 401, or exhausted retries)."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class GitHubRateLimitExceeded(GitHubError):
    """Exception raised when rate limit is exceeded and waiting is disabled or exceeds threshold."""
    def __init__(self, message: str, reset_timestamp: Optional[int] = None, wait_seconds: Optional[float] = None):
        super().__init__(message)
        self.reset_timestamp = reset_timestamp
        self.wait_seconds = wait_seconds


def parse_retry_after(retry_after_str: str) -> float:
    """
    Parse a Retry-After header string.
    Can be an integer (seconds) or an HTTP-date.
    """
    retry_after_str = retry_after_str.strip()
    try:
        # Check if it's integer seconds
        return max(0.0, float(retry_after_str))
    except ValueError:
        pass

    try:
        # Parse HTTP-date format (e.g., RFC 1123 / RFC 2822)
        parsed_date = email.utils.parsedate_to_datetime(retry_after_str)
        now_ts = time.time()
        wait = parsed_date.timestamp() - now_ts
        return max(0.0, wait)
    except Exception as e:
        logger.warning("Could not parse Retry-After header '%s': %s. Defaulting to 60s.", retry_after_str, e)
        return 60.0


class GitHubClient:
    """
    Client for interacting with the GitHub REST API using read-only requests.
    Supports context management, automatic rate limit backoff, session closing,
    and configurable inter-request pacing delays.
    """

    DEFAULT_BASE_URL = "https://api.github.com"
    API_VERSION = "2022-11-28"
    DEFAULT_USER_AGENT = "GitHubRepoFetcher/1.0 (+https://github.com)"

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        max_retries: int = 5,
        backoff_factor: float = 1.0,
        max_backoff: float = 60.0,
        timeout: float = 30.0,
        wait_on_rate_limit: bool = True,
        max_rate_limit_wait: float = 3600.0,
        user_agent: str = DEFAULT_USER_AGENT,
        request_delay: float = 0.0
    ):
        """
        Initialize the GitHub REST API client.

        :param token: Optional API token. If not provided, checks GITHUB_TOKEN and GH_TOKEN env vars.
        :param base_url: Base URL for GitHub API (default: https://api.github.com).
        :param max_retries: Maximum number of retry attempts for retryable errors.
        :param backoff_factor: Multiplier for exponential backoff calculations.
        :param max_backoff: Maximum backoff sleep time in seconds for exponential backoff.
        :param timeout: Request timeout in seconds.
        :param wait_on_rate_limit: Whether to sleep and wait when rate limit resets instead of failing.
        :param max_rate_limit_wait: Maximum seconds to sleep for a rate limit reset before failing.
        :param user_agent: User-Agent header value for requests.
        :param request_delay: Optional polite pacing delay in seconds between consecutive requests.
        """
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.max_backoff = max_backoff
        self.timeout = timeout
        self.wait_on_rate_limit = wait_on_rate_limit
        self.max_rate_limit_wait = max_rate_limit_wait
        self.user_agent = user_agent
        self.request_delay = max(0.0, float(request_delay))

        # Resolve authentication token without hardcoding
        resolved_token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self._is_authenticated = bool(resolved_token)

        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.API_VERSION,
            "User-Agent": self.user_agent,
        })

        if resolved_token:
            self.session.headers["Authorization"] = f"Bearer {resolved_token.strip()}"
            masked_token = f"{resolved_token[:4]}...{resolved_token[-4:]}" if len(resolved_token) > 8 else "***"
            logger.info("GitHub client initialized with authenticated token (%s). Rate limit: 5,000 req/hr.", masked_token)
        else:
            logger.warning("No GitHub token provided. Operating unauthenticated. Rate limit: 60 req/hr. "
                           "Set GITHUB_TOKEN environment variable to increase limit to 5,000 req/hr.")

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP session to release resources immediately."""
        if hasattr(self, "session") and self.session is not None:
            self.session.close()
            logger.debug("GitHub client session closed.")

    @property
    def is_authenticated(self) -> bool:
        """Returns True if the client has an authentication token configured."""
        return self._is_authenticated

    def _calculate_backoff(self, attempt: int) -> float:
        """
        Calculate exponential backoff with full jitter:
        min(max_backoff, backoff_factor * 2^(attempt-1)) + jitter
        """
        base_delay = min(self.max_backoff, self.backoff_factor * (2 ** (attempt - 1)))
        jitter = random.uniform(0.1, 1.0)
        return round(base_delay + jitter, 2)

    def _extract_rate_limit_info(self, headers: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        """
        Extract x-ratelimit headers: (limit, remaining, reset_timestamp)
        """
        try:
            limit = int(headers["x-ratelimit-limit"]) if "x-ratelimit-limit" in headers else None
            remaining = int(headers["x-ratelimit-remaining"]) if "x-ratelimit-remaining" in headers else None
            reset_ts = int(headers["x-ratelimit-reset"]) if "x-ratelimit-reset" in headers else None
            return limit, remaining, reset_ts
        except (ValueError, TypeError):
            return None, None, None

    def _handle_rate_limit_wait(self, reset_timestamp: Optional[int], retry_after: Optional[str]) -> bool:
        """
        Determine sleep duration when rate limit or 429 is encountered,
        and wait if allowed by policy. Returns True if successfully waited and should retry.
        """
        wait_seconds = 0.0

        if retry_after:
            wait_seconds = parse_retry_after(retry_after)
            logger.warning("Rate limit hit: 'Retry-After' specified %s seconds.", wait_seconds)
        elif reset_timestamp:
            now_ts = time.time()
            wait_seconds = max(0.0, (reset_timestamp - now_ts) + 1.0)
            logger.warning("Rate limit exhausted: resets in %.1f seconds (at %s).",
                           wait_seconds, time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(reset_timestamp)))
        else:
            wait_seconds = 60.0
            logger.warning("Rate limit hit with no explicit reset time. Defaulting to 60s wait.")

        if not self.wait_on_rate_limit:
            raise GitHubRateLimitExceeded(
                f"Rate limit exceeded. Reset in {wait_seconds:.1f}s, but wait_on_rate_limit is False.",
                reset_timestamp=reset_timestamp,
                wait_seconds=wait_seconds
            )

        if wait_seconds > self.max_rate_limit_wait:
            raise GitHubRateLimitExceeded(
                f"Rate limit wait of {wait_seconds:.1f}s exceeds maximum allowed wait of {self.max_rate_limit_wait:.1f}s.",
                reset_timestamp=reset_timestamp,
                wait_seconds=wait_seconds
            )

        logger.info("Sleeping for %.1f seconds until rate limit resets...", wait_seconds)
        time.sleep(wait_seconds)
        return True

    def get(self, url: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        """
        Execute a read-only HTTP GET request with retries, exponential backoff,
        and rate-limit handling.
        """
        attempt = 0
        endpoint = urlparse(url).path

        while attempt < self.max_retries:
            attempt += 1
            logger.debug("HTTP GET [%s] (attempt %d/%d) params=%s", endpoint, attempt, self.max_retries, params)

            start_time = time.time()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                duration = time.time() - start_time
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                duration = time.time() - start_time
                logger.warning("Network error on GET [%s] after %.2fs: %s", endpoint, duration, exc)
                if attempt >= self.max_retries:
                    raise GitHubAPIError(f"Network error after {self.max_retries} attempts: {exc}") from exc
                backoff = self._calculate_backoff(attempt)
                logger.info("Retrying in %.2fs (attempt %d/%d)...", backoff, attempt, self.max_retries)
                time.sleep(backoff)
                continue
            except requests.exceptions.RequestException as exc:
                logger.error("Unrecoverable request exception on GET [%s]: %s", endpoint, exc)
                raise GitHubAPIError(f"Request exception: {exc}") from exc

            # Log rate limit metadata
            limit, remaining, reset_ts = self._extract_rate_limit_info(response.headers)
            logger.debug(
                "HTTP %d for [%s] (took %.2fs) | RateLimit: %s/%s, Reset: %s",
                response.status_code, endpoint, duration, remaining, limit, reset_ts
            )

            # Success
            if response.status_code == 200:
                if remaining is not None and remaining <= 5:
                    logger.warning("Approaching rate limit: only %d requests remaining in window.", remaining)
                return response

            # Rate limiting: HTTP 429 Too Many Requests
            if response.status_code == 429:
                logger.warning("Received HTTP 429 Too Many Requests on GET [%s].", endpoint)
                retry_after = response.headers.get("Retry-After")
                self._handle_rate_limit_wait(reset_ts, retry_after)
                continue

            # HTTP 403 Forbidden: check if it's primary rate limit or secondary abuse limit
            if response.status_code == 403:
                body_text = response.text.lower()
                is_rate_limit = (remaining == 0) or ("rate limit" in body_text) or ("abuse detection" in body_text)
                if is_rate_limit:
                    logger.warning("Received HTTP 403 Rate Limit / Abuse Mechanism on GET [%s].", endpoint)
                    retry_after = response.headers.get("Retry-After")
                    self._handle_rate_limit_wait(reset_ts, retry_after)
                    continue

                logger.error("HTTP 403 Forbidden (not rate-limiting) on GET [%s]: %s", endpoint, response.text)
                raise GitHubAPIError(f"HTTP 403 Forbidden: {response.text}", status_code=403, response_body=response.text)

            # Server errors: 500, 502, 503, 504 (retryable)
            if response.status_code in (500, 502, 503, 504):
                logger.warning("Server error HTTP %d on GET [%s].", response.status_code, endpoint)
                if attempt >= self.max_retries:
                    raise GitHubAPIError(
                        f"Server error HTTP {response.status_code} after {self.max_retries} attempts: {response.text}",
                        status_code=response.status_code,
                        response_body=response.text
                    )
                backoff = self._calculate_backoff(attempt)
                logger.info("Retrying server error in %.2fs (attempt %d/%d)...", backoff, attempt, self.max_retries)
                time.sleep(backoff)
                continue

            # Other 4xx Client Errors (401, 404, 422, etc.) - non-retryable
            logger.error("Client error HTTP %d on GET [%s]: %s", response.status_code, endpoint, response.text)
            raise GitHubAPIError(
                f"Client error HTTP {response.status_code} for URL {url}: {response.text}",
                status_code=response.status_code,
                response_body=response.text
            )

        raise GitHubAPIError(f"Exceeded max retries ({self.max_retries}) for URL: {url}")

    def paginate(
        self,
        initial_url: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: Optional[int] = None,
        max_items: Optional[int] = None,
        start_page: int = 1,
        request_delay: Optional[float] = None,
        page_callback: Optional[Callable[[int, Optional[str], List[Dict[str, Any]]], None]] = None
    ) -> Iterator[Dict[str, Any]]:
        """
        Paginate through GitHub API list endpoints using the standard Link header.

        :param initial_url: The starting URL endpoint.
        :param params: Initial query parameters (e.g. per_page).
        :param max_pages: Optional limit on the number of pages to retrieve.
        :param max_items: Optional limit on total items to yield.
        :param start_page: Initial page number (useful when resuming).
        :param request_delay: Optional pause (in seconds) between consecutive page fetches.
        :param page_callback: Optional callback(page_num, next_url, items) called when each page completes.
        :yield: Individual item dictionaries from the paginated lists.
        """
        current_url: Optional[str] = initial_url
        current_params: Optional[Dict[str, Any]] = params
        page_num = start_page
        total_items_yielded = 0
        pacing_delay = self.request_delay if request_delay is None else max(0.0, float(request_delay))

        while current_url:
            if max_pages is not None and page_num > max_pages:
                logger.info("Reached maximum page limit (%d). Stopping pagination.", max_pages)
                break

            logger.info("Fetching page %d from %s", page_num, current_url)
            response = self.get(current_url, params=current_params)
            items = response.json()

            if not isinstance(items, list):
                logger.error("Expected JSON list from %s, received %s", current_url, type(items))
                raise GitHubAPIError(f"Unexpected response structure: expected list, got {type(items)}")

            if not items:
                logger.info("Received empty list on page %d. Pagination complete.", page_num)
                break

            for item in items:
                yield item
                total_items_yielded += 1
                if max_items is not None and total_items_yielded >= max_items:
                    logger.info("Reached requested total items limit (%d). Stopping pagination.", max_items)
                    # Notify callback for partial / completed page before exiting
                    if page_callback:
                        next_link = response.links.get("next")
                        next_url = next_link["url"] if next_link and "url" in next_link else None
                        page_callback(page_num, next_url, items)
                    return

            # Check RFC 5988 Link header for rel="next"
            next_link = response.links.get("next")
            next_url = next_link["url"] if next_link and "url" in next_link else None

            # Fire page completion callback (useful for checkpointing)
            if page_callback:
                page_callback(page_num, next_url, items)

            if next_url:
                if pacing_delay > 0:
                    logger.info("Applying request delay of %.2fs before next page...", pacing_delay)
                    time.sleep(pacing_delay)

                current_url = next_url
                current_params = None  # query params are embedded in the next_link URL
                page_num += 1
            else:
                logger.info("No 'next' link found in response header. Reached end of pagination.")
                break

    def get_organization_repositories(
        self,
        org: str,
        per_page: int = 100,
        repo_type: str = "public",
        max_pages: Optional[int] = None,
        max_repos: Optional[int] = None,
        start_page: int = 1,
        start_url: Optional[str] = None,
        request_delay: Optional[float] = None,
        page_callback: Optional[Callable[[int, Optional[str], List[Dict[str, Any]]], None]] = None
    ) -> Iterator[Dict[str, Any]]:
        """
        Retrieve repositories belonging to an organization with resume and pacing support.

        :param org: Organization name (e.g., 'microsoft').
        :param per_page: Items per page (max 100).
        :param repo_type: Repository type filter (default: 'public').
        :param max_pages: Optional upper bound on pages to fetch.
        :param max_repos: Optional upper bound on total repositories to fetch.
        :param start_page: Page number to start from (default: 1).
        :param start_url: Optional explicit start URL (e.g. from checkpoint).
        :param request_delay: Optional pause (in seconds) between consecutive page requests.
        :param page_callback: Optional callback(page_num, next_url, items) called on page completion.
        :return: Generator yielding raw repository dictionaries.
        """
        if start_url:
            url = start_url
            params = None
        else:
            url = f"{self.base_url}/orgs/{org}/repos"
            params = {
                "type": repo_type,
                "per_page": min(100, max(1, per_page)),
            }
            if start_page > 1:
                params["page"] = start_page

        logger.info("Starting repository fetch for organization '%s' (type=%s, per_page=%d, start_page=%d)",
                    org, repo_type, per_page, start_page)
        return self.paginate(
            url,
            params=params,
            max_pages=max_pages,
            max_items=max_repos,
            start_page=start_page,
            request_delay=request_delay,
            page_callback=page_callback
        )
