import requests
import json
import time
import logging
import re
from typing import Optional, Dict, Any, Generator, List
from urllib.parse import quote, urljoin, urlparse, parse_qs

logger = logging.getLogger(__name__)

TOTAL_DOWNLOADS_PATTERN = re.compile(r'Total downloads</span>\s*<h3 title="([\d,]+)"')


class GitHubRestApi:
    """
    A wrapper for the GitHub REST API with built-in rate limiting and pagination support.
    Caches repository data to minimize API calls.
    
    Args:
        token: GitHub personal access token for authentication
        owner: Default owner/org name for API requests (optional)
        base_url: Base URL for GitHub API (default: https://api.github.com)
    """
    
    def __init__(self, token: Optional[str] = None, owner: Optional[str] = None, base_url: str = "https://api.github.com", cache_timeout_sec: int = 300):
        """Initialize the GitHub API client with session and default headers."""
        self.token = token
        self.owner = owner
        self.base_url = base_url.rstrip('/')
        self.cache_timeout_sec = cache_timeout_sec
        # Cache for repository data to minimize API calls
        self.cached_repositories = {}
        self.cached_owners = {}
        self.cached_packages = {}
        
        # Create persistent session for connection pooling
        self.session = requests.Session()
        
        # Set default headers
        headers = {
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28'
        }
        if token:
            headers['Authorization'] = f'token {token}'
        self.session.headers.update(headers)
        
        # Rate limiting tracking
        self.rate_limit_remaining = None
        self.rate_limit_reset = None
        self.last_request_time = 0
        self.min_sleep_time = 0.5  # Minimum 0.5s between requests
        
        logger.info(f"Initialized GitHub API client for base URL: {self.base_url}")
    
    def _sleep_between_requests(self) -> None:
        """Ensure minimum time between requests (0.5 seconds)."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_sleep_time:
            sleep_time = self.min_sleep_time - elapsed
            logger.debug(f"Sleeping for {sleep_time:.2f}s to maintain rate limit")
            time.sleep(sleep_time)
    
    def _update_rate_limit_info(self, response: requests.Response) -> None:
        """Extract and update rate limit information from response headers."""
        self.rate_limit_remaining = int(response.headers.get('X-RateLimit-Remaining', 0))
        self.rate_limit_reset = int(response.headers.get('X-RateLimit-Reset', 0))
        
        logger.debug(
            f"Rate limit: {self.rate_limit_remaining} remaining, "
            f"resets at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.rate_limit_reset))}"
        )
    
    def _check_rate_limit(self) -> None:
        """Check rate limit and pause if necessary to avoid hitting the limit."""
        if self.rate_limit_remaining is not None and self.rate_limit_remaining < 10:
            if self.rate_limit_reset:
                wait_time = self.rate_limit_reset - time.time()
                if wait_time > 0:
                    logger.warning(
                        f"Rate limit low ({self.rate_limit_remaining} remaining). "
                        f"Sleeping for {wait_time:.0f}s until reset."
                    )
                    time.sleep(wait_time + 1)  # Add 1s buffer
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        max_retries: int = 3
    ) -> requests.Response:
        """
        Make a request to the GitHub API with rate limiting and retry logic.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            endpoint: API endpoint (e.g., '/repos/owner/repo')
            params: Query parameters
            data: Request body data
            max_retries: Maximum number of retry attempts
            
        Returns:
            Response object
            
        Raises:
            requests.HTTPError: If the request fails after all retries
        """
        # Ensure minimum time between requests
        self._sleep_between_requests()
        
        # Check rate limit before making request
        self._check_rate_limit()
        
        # Construct full URL
        url = urljoin(self.base_url, endpoint.lstrip('/'))
        
        retry_count = 0
        backoff_time = 1
        
        while True:
            try:
                logger.debug(f"Making {method} request to {url}")
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data
                )
                
                self.last_request_time = time.time()
                
                # Update rate limit info
                self._update_rate_limit_info(response)
                
                # Handle explicit rate limiting and retryable secondary limits.
                if response.status_code == 429:
                    retry_after = response.headers.get('Retry-After')
                    if retry_after:
                        wait_time = int(retry_after)
                    else:
                        wait_time = backoff_time

                    if retry_count >= max_retries:
                        response.raise_for_status()
                    
                    logger.warning(
                        f"Rate limited (status {response.status_code}). "
                        f"Waiting {wait_time}s before retry {retry_count + 1}/{max_retries}"
                    )
                    time.sleep(wait_time)
                    retry_count += 1
                    backoff_time *= 2  # Exponential backoff
                    continue

                if response.status_code == 403:
                    response_text = response.text.lower()
                    retry_after = response.headers.get('Retry-After')
                    is_retryable_limit = (
                        retry_after is not None
                        or 'secondary rate limit' in response_text
                        or 'rate limit exceeded' in response_text
                        or 'abuse detection' in response_text
                    )

                    if is_retryable_limit:
                        if retry_after:
                            wait_time = int(retry_after)
                        else:
                            wait_time = backoff_time

                        if retry_count >= max_retries:
                            response.raise_for_status()

                        logger.warning(
                            f"Rate limited (status {response.status_code}). "
                            f"Waiting {wait_time}s before retry {retry_count + 1}/{max_retries}"
                        )
                        time.sleep(wait_time)
                        retry_count += 1
                        backoff_time *= 2  # Exponential backoff
                        continue
                
                # Raise for other HTTP errors
                response.raise_for_status()
                return response
                
            except requests.exceptions.RequestException as e:
                retry_count += 1
                if retry_count > max_retries:
                    logger.error(f"Request failed after {max_retries} retries: {e}")
                    raise
                
                logger.warning(f"Request failed, retrying ({retry_count}/{max_retries}): {e}")
                time.sleep(backoff_time)
                backoff_time *= 2  # Exponential backoff
        
        raise requests.HTTPError(f"Request failed after {max_retries} retries")
    
    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make a GET request to the GitHub API.
        
        Args:
            endpoint: API endpoint
            params: Query parameters
            
        Returns:
            JSON response as dictionary
        """
        response = self._make_request('GET', endpoint, params=params)
        return response.json()
    
    def post(self, endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make a POST request to the GitHub API.
        
        Args:
            endpoint: API endpoint
            data: Request body data
            
        Returns:
            JSON response as dictionary
        """
        response = self._make_request('POST', endpoint, data=data)
        return response.json()
    
    def put(self, endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make a PUT request to the GitHub API.
        
        Args:
            endpoint: API endpoint
            data: Request body data
            
        Returns:
            JSON response as dictionary
        """
        response = self._make_request('PUT', endpoint, data=data)
        return response.json()
    
    def delete(self, endpoint: str) -> None:
        """
        Make a DELETE request to the GitHub API.
        
        Args:
            endpoint: API endpoint
        """
        self._make_request('DELETE', endpoint)
    
    def _paginated_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: Optional[int] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Generator for paginated GitHub API requests.
        
        Args:
            endpoint: API endpoint
            params: Query parameters
            max_pages: Maximum number of pages to fetch (None for all pages)
            
        Yields:
            Individual items from each page
        """
        if params is None:
            params = {}
        
        # Set per_page to maximum allowed by GitHub API
        params.setdefault('per_page', 100)
        
        current_page = 0
        next_url = endpoint
        
        while next_url and (max_pages is None or current_page < max_pages):
            current_page += 1
            logger.info(f"Fetching page {current_page} from {endpoint}")
            
            # Make request
            response = self._make_request('GET', next_url, params=params if current_page == 1 else None)
            
            # Parse JSON response
            data = response.json()
            
            # Yield each item
            if isinstance(data, list):
                for item in data:
                    yield item
            else:
                # Some endpoints return objects with items in a specific key
                yield data
            
            # Check for next page in Link header
            link_header = response.headers.get('Link', '')
            next_url = None
            
            if link_header:
                # Parse Link header for next page
                links = {}
                for link in link_header.split(','):
                    url_part, rel_part = link.split(';')
                    url = url_part.strip()[1:-1]  # Remove < >
                    rel = rel_part.split('=')[1].strip('"')
                    links[rel] = url
                
                next_url = links.get('next')
                if next_url:
                    logger.debug(f"Next page URL: {next_url}")
    
    def get_paginated(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all results from a paginated endpoint as a list.
        
        Args:
            endpoint: API endpoint
            params: Query parameters
            max_pages: Maximum number of pages to fetch (None for all pages)
            
        Returns:
            List of all items from all pages
        """
        return list(self._paginated_request(endpoint, params, max_pages))
    
    def close(self) -> None:
        """Close the requests session."""
        self.session.close()
        logger.info("Closed GitHub API session")
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def get_owner(self, owner: Optional[str] = None) -> Optional[str]:
        """
        Get the default owner/org name.
        
        Returns:
            Owner/org name or None if not set
        """
        if owner is None:
            if self.owner is None:
                raise ValueError("Owner must be specified either in method call or during initialization.")
            owner = self.owner
        return owner

    def get_owner_metadata(self, owner: str = None) -> Dict[str, Any]:
        """
        Get metadata for the specified owner.

        Args:
            owner: The owner name

        Returns:
            Owner metadata from the GitHub API
        """
        owner = self.get_owner(owner)
        curr_time = time.time()
        cached = self.cached_owners.get(owner)
        if cached:
            cached_time, data = cached
            if curr_time - cached_time < self.cache_timeout_sec:
                return data

        data = self.get(f"/users/{owner}")
        self.cached_owners[owner] = (curr_time, data)
        return data

    def get_owner_type(self, owner: str = None) -> str:
        """
        Get the owner type for the specified owner.

        Args:
            owner: The owner name

        Returns:
            Owner type, usually User or Organization
        """
        owner_data = self.get_owner_metadata(owner)
        return owner_data.get("type", "User")

    def _get_owner_packages_endpoint(self, owner: str = None) -> str:
        """
        Get the correct package endpoint prefix for a user or organization owner.

        Args:
            owner: The owner name

        Returns:
            API endpoint prefix for package requests
        """
        owner = self.get_owner(owner)
        owner_type = self.get_owner_type(owner)
        if owner_type == "Organization":
            return f"/orgs/{owner}"
        return f"/users/{owner}"

    def get_all_container_packages(self, owner: str = None) -> List[Dict[str, Any]]:
        """
        Get all GHCR container packages for the specified owner.

        Args:
            owner: The owner name

        Returns:
            List of container packages
        """
        endpoint_prefix = self._get_owner_packages_endpoint(owner)
        return self.get_paginated(
            f"{endpoint_prefix}/packages",
            params={"package_type": "container"}
        )

    def get_container_package(self, package_name: str, owner: str = None) -> Dict[str, Any]:
        """
        Get metadata for a GHCR container package.

        Args:
            package_name: The package name
            owner: The owner name

        Returns:
            Package metadata
        """
        owner = self.get_owner(owner)
        cache_key = f"{owner}/{package_name}"
        curr_time = time.time()
        cached = self.cached_packages.get(cache_key)
        if cached:
            cached_time, data = cached
            if curr_time - cached_time < self.cache_timeout_sec:
                return data

        endpoint_prefix = self._get_owner_packages_endpoint(owner)
        encoded_package_name = quote(package_name, safe='')
        data = self.get(f"{endpoint_prefix}/packages/container/{encoded_package_name}")
        self.cached_packages[cache_key] = (curr_time, data)
        return data

    def get_container_package_versions(self, package_name: str, owner: str = None) -> List[Dict[str, Any]]:
        """
        Get all versions for a GHCR container package.

        Args:
            package_name: The package name
            owner: The owner name

        Returns:
            List of package versions
        """
        endpoint_prefix = self._get_owner_packages_endpoint(owner)
        encoded_package_name = quote(package_name, safe='')
        return self.get_paginated(
            f"{endpoint_prefix}/packages/container/{encoded_package_name}/versions"
        )

    def get_container_package_page_download_count(self, package_url: str) -> Optional[int]:
        """
        Get the total download count for a GHCR container package from the package page.

        Args:
            package_url: The GitHub package page URL

        Returns:
            The parsed total download count, or None if it could not be found
        """
        self._sleep_between_requests()
        response = self.session.get(
            package_url,
            headers={
                'Accept': 'text/html,application/xhtml+xml',
                'User-Agent': 'Mozilla/5.0'
            },
            allow_redirects=True
        )
        self.last_request_time = time.time()
        response.raise_for_status()

        match = TOTAL_DOWNLOADS_PATTERN.search(response.text)
        if not match:
            return None

        return int(match.group(1).replace(',', ''))

    def get_container_package_download_count(self, package_name: str, owner: str = None) -> int:
        """
        Get the total download count for a GHCR container package.

        Args:
            package_name: The package name
            owner: The owner name

        Returns:
            Download count aggregated from package metadata or versions
        """
        package_data = self.get_container_package(package_name, owner)
        package_url = package_data.get("html_url")
        if package_url:
            page_download_count = self.get_container_package_page_download_count(package_url)
            if page_download_count is not None:
                return page_download_count

        if "download_count" in package_data:
            return package_data.get("download_count", 0)

        versions = self.get_container_package_versions(package_name, owner)
        return sum(version.get("download_count", 0) for version in versions)
    
    def get_repo(self, repo: str, owner: str = None) -> Dict[str, Any]:
        """
        Get the cached repository data or fetch it if not cached.
        
        Args:
            owner: The owner of the repository
            repo: The name of the repository
            
        Returns:
            Dictionary with repository data
        """
        owner = self.get_owner(owner)
        cache_key = f"{owner}/{repo}"
        curr_time = time.time()
        cached = self.cached_repositories.get(cache_key)
        if cached:
            cached_time, data = cached
            if curr_time - cached_time < self.cache_timeout_sec:
                return data
        url = f"/repos/{owner}/{repo}"
        response = self._make_request('GET', url)
        data = response.json()
        self.cached_repositories[cache_key] = (curr_time, data)
        return data
    
    def get_all_repos_for_user(self, owner: str = None) -> List[Dict[str, Any]]:
        """
        Get all repositories for the specified user (always fresh, caches each repo).
        
        Args:
            owner: The owner name
            
        Returns:
            List of repositories
        """
        owner = self.get_owner(owner)
        logger.info(f"Fetching fresh repository list for {owner}")
        repos = self.get_paginated(f"/users/{owner}/repos")
        
        # Cache each individual repository for future get_repo() calls
        curr_time = time.time()
        for repo_data in repos:
            repo_name = repo_data.get('name')
            if repo_name:
                cache_key = f"{owner}/{repo_name}"
                self.cached_repositories[cache_key] = (curr_time, repo_data)
                logger.debug(f"Cached repository: {cache_key}")
        
        return repos

    def get_repo_star_count(self, repo: str, owner: str = None) -> int:
        """
        Get the number of stars for a GitHub repository.
        
        Args:
            owner: The owner of the repository
            repo: The name of the repository
            
        Returns:
            Number of stars
        """
        repo_data = self.get_repo(repo, owner)
        return repo_data.get("stargazers_count", 0)
    
    def get_repo_fork_count(self, repo: str, owner: str = None) -> int:
        """
        Get the number of forks for a GitHub repository.
        
        Args:
            owner: The owner of the repository
            repo: The name of the repository
            
        Returns:
            Number of forks
        """
        repo_data = self.get_repo(repo, owner)
        return repo_data.get("forks_count", 0)
    
    def get_repo_watchers_count(self, repo: str, owner: str = None) -> int:
        """
        Get the number of watchers for a GitHub repository.
        
        Args:
            owner: The owner of the repository
            repo: The name of the repository
            
        Returns:
            Number of watchers
        """
        repo_data = self.get_repo(repo, owner)
        return repo_data.get("watchers_count", 0)
    
    def get_repo_open_issues_count(self, repo: str, owner: str = None) -> int:
        """
        Get the number of open issues for a GitHub repository.
        
        Args:
            owner: The owner of the repository
            repo: The name of the repository
            
        Returns:
            Number of open issues
        """
        repo_data = self.get_repo(repo, owner)
        return repo_data.get("open_issues_count", 0)
    
    def get_repo_description(self, repo: str, owner: str = None) -> str:
        """
        Get the description of a GitHub repository.
        
        Args:
            owner: The owner of the repository
            repo: The name of the repository
            
        Returns:
            Repository description
        """
        repo_data = self.get_repo(repo, owner)
        return repo_data.get("description", "")
    
    def get_repo_last_pushed(self, repo: str, owner: str = None) -> str:
        """
        Get the last pushed timestamp of a GitHub repository.
        
        Args:
            owner: The owner of the repository
            repo: The name of the repository
            
        Returns:
            Last pushed timestamp
        """
        repo_data = self.get_repo(repo, owner)
        return repo_data.get("pushed_at", "")
