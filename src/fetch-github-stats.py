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
    
    # Track multiple users
    owners = ["chase-roohms", "transmute-app"]
    
    # Fetch stats for each repo
    new_repositories = {}
    sum_stars = 0
    sum_forks = 0
    sum_watchers = 0
    sum_open_issues = 0
    
    for owner in owners:
        requester = gh_api.GitHubRestApi(owner=owner)
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
            
        requester.close()
    
    # Calculate totals
    totals = {
        "total_stars": sum_stars,
        "total_forks": sum_forks,
        "total_watchers": sum_watchers,
        "total_open_issues": sum_open_issues
    }
    
    print(f"\nTotal Stars: {sum_stars}, Total Forks: {sum_forks}, Total Watchers: {sum_watchers}, Total Open Issues: {sum_open_issues}")
    
    # Check if there are any actual changes to repository data
    old_repositories = old_stats.get("repositories", {})
    old_totals = old_stats.get("totals", {})
    has_changes = new_repositories != old_repositories or totals != old_totals
    
    # Only update timestamp if there are changes
    stats = {
        "last_updated": datetime.now(UTC).isoformat() if has_changes else old_stats.get("last_updated", datetime.now(UTC).isoformat()),
        "totals": totals,
        "repositories": new_repositories
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