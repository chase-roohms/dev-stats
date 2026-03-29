#!/usr/bin/env python3
"""Fetch GitHub statistics for repositories"""

import gh_api
import json
import os
from datetime import datetime, UTC


def main():
    # Load existing stats if they exist
    old_stats = {}
    stats_file = 'data/github-stats.json'
    if os.path.exists(stats_file):
        with open(stats_file, 'r') as f:
            old_stats = json.load(f)
    old_containers = old_stats.get("containers", {})
    old_container_totals = old_stats.get("container_totals", {})
    
    # Check for GitHub token (falls back to unauthenticated if not provided)
    github_token = os.environ.get('GITHUB_TOKEN')
    if github_token:
        print("✓ Using authenticated GitHub API requests")
    else:
        print("⚠ No GITHUB_TOKEN found, using unauthenticated requests (lower rate limits)")
        print("⚠ GHCR container stats require a GitHub token with package read access; container downloads will be skipped")
    
    # Track multiple users
    owners = ["chase-roohms", "transmute-app"]
    
    # Fetch stats for each repo
    new_repositories = {}
    sum_stars = 0
    sum_forks = 0
    sum_watchers = 0
    sum_open_issues = 0
    new_containers = {} if github_token else old_containers
    sum_container_downloads = 0
    
    for owner in owners:
        requester = gh_api.GitHubRestApi(token=github_token, owner=owner)
        repos = requester.get_all_repos_for_user()
        repo_names = [repo["name"] for repo in repos if repo["name"] != "dev-stats"]
        
        for repo in repo_names:
            print(f"Fetching stats for {requester.owner}/{repo}...")
            try:
                stars = requester.get_repo_star_count(repo=repo)
                sum_stars += stars
                forks = requester.get_repo_fork_count(repo=repo)
                sum_forks += forks
                watchers = requester.get_repo_watchers_count(repo=repo)
                sum_watchers += watchers
                open_issues = requester.get_repo_open_issues_count(repo=repo)
                sum_open_issues += open_issues
                description = requester.get_repo_description(repo=repo)
                last_pushed = requester.get_repo_last_pushed(repo=repo)
                
                new_repositories[f'{requester.owner}/{repo}'] = {
                    "stars": stars,
                    "forks": forks,
                    "watchers": watchers,
                    "open_issues": open_issues,
                    "description": description,
                    "last_updated": last_pushed
                }
                print(f"  ✓ {requester.owner}/{repo}: {stars} stars, {forks} forks")
            except Exception as e:
                print(f"  ✗ Error fetching {requester.owner}/{repo}: {e}")
                new_repositories[f'{requester.owner}/{repo}'] = {
                    "error": str(e)
                }

        if github_token:
            print(f"Fetching GHCR container stats for {requester.owner}...")
            try:
                containers = requester.get_all_container_packages()
                for container in containers:
                    package_name = container["name"]
                    container_key = f"{requester.owner}/{package_name}"
                    print(f"Fetching container stats for {container_key}...")
                    try:
                        download_count = requester.get_container_package_download_count(package_name=package_name)
                        sum_container_downloads += download_count
                        new_containers[container_key] = {
                            "download_count": download_count,
                            "description": container.get("repository", {}).get("description"),
                            "last_updated": container.get("updated_at", ""),
                            "version_count": container.get("version_count", 0),
                            "visibility": container.get("visibility", ""),
                            "url": container.get("html_url", "")
                        }
                        print(f"  ✓ {container_key}: {download_count} downloads")
                    except Exception as e:
                        print(f"  ✗ Error fetching {container_key}: {e}")
                        new_containers[container_key] = {
                            "error": str(e)
                        }
            except Exception as e:
                print(f"  ✗ Error listing GHCR containers for {requester.owner}: {e}")
            
        requester.close()
    
    # Calculate totals
    totals = {
        "total_stars": sum_stars,
        "total_forks": sum_forks,
        "total_watchers": sum_watchers,
        "total_open_issues": sum_open_issues
    }
    container_totals = {
        "total_downloads": sum_container_downloads,
        "total_containers": len(new_containers)
    } if github_token else old_container_totals
    
    print(f"\nTotal Stars: {sum_stars}, Total Forks: {sum_forks}, Total Watchers: {sum_watchers}, Total Open Issues: {sum_open_issues}")
    if github_token:
        print(f"Total GHCR Downloads: {sum_container_downloads}, Total Containers: {len(new_containers)}")
    
    # Check if there are any actual changes to repository data
    old_repositories = old_stats.get("repositories", {})
    old_totals = old_stats.get("totals", {})
    has_changes = (
        new_repositories != old_repositories
        or totals != old_totals
        or new_containers != old_containers
        or container_totals != old_container_totals
    )
    
    # Only update timestamp if there are changes
    stats = {
        "last_updated": datetime.now(UTC).isoformat() if has_changes else old_stats.get("last_updated", datetime.now(UTC).isoformat()),
        "totals": totals,
        "repositories": new_repositories,
        "container_totals": container_totals,
        "containers": new_containers
    }
    
    # Write stats to json file
    with open(stats_file, 'w') as f:
        json.dump(stats, f, indent=2)
    
    if has_changes:
        print(f"\nChanges detected! Stats saved to {stats_file}")
    else:
        print(f"\nNo changes detected. {stats_file} unchanged.")


if __name__ == "__main__":
    main()