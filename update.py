"""Optional upstream update before boot: UPSTREAM_REPO / UPSTREAM_BRANCH from the environment."""

import os
import subprocess
import sys


def main() -> int:
    repo, branch = os.environ.get("UPSTREAM_REPO", "").strip(), os.environ.get("UPSTREAM_BRANCH", "main").strip()
    if not repo:
        return 0
    steps = [["git", "init", "-q"], ["git", "remote", "remove", "upstream"], ["git", "remote", "add", "upstream", repo],
             ["git", "fetch", "-q", "--depth", "1", "upstream", branch], ["git", "reset", "-q", "--hard", "FETCH_HEAD"]]
    for step in steps:
        result = subprocess.run(step, capture_output=True, text=True, check=False)
        if result.returncode and step[2] != "remove":
            print(f"update skipped: {' '.join(step[:3])}: {result.stderr.strip()}", file=sys.stderr)
            return 0
    print(f"updated from {repo.split('@')[-1]} ({branch})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
