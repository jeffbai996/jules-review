"""
Jules code review integration.

Usage:
    python jules.py --repo ibkr-terminal --prompt "Review for bugs and security issues"
    python jules.py --repo ibkr-terminal  # uses default review prompt
    python jules.py --repo ibkr-terminal --branch feature/my-branch

Autodetect: if --repo is omitted, reads git remote origin from cwd to infer repo name.
Returns review summary to stdout; optionally posts to Discord if --discord-channel is set.
"""
import os
import sys
import time
import argparse
import subprocess
import requests
import logging
from dotenv import load_dotenv

load_dotenv()

JULES_API_KEY = os.getenv("JULES_API_KEY")
if not JULES_API_KEY:
    raise RuntimeError("JULES_API_KEY not set in .env or environment")

GITHUB_USER = os.getenv("JULES_GITHUB_USER")
if not GITHUB_USER:
    raise RuntimeError("JULES_GITHUB_USER not set in .env or environment")
BASE_URL     = "https://jules.googleapis.com/v1alpha"
HEADERS      = {"X-Goog-Api-Key": JULES_API_KEY, "Content-Type": "application/json"}
POLL_INTERVAL = 8   # seconds between activity polls
POLL_TIMEOUT  = 600  # 10 min max wait

DEFAULT_REVIEW_PROMPT = (
    "Review this codebase for: (1) bugs or logic errors, (2) security vulnerabilities, "
    "(3) code quality issues, (4) anything that would break in production. "
    "Be direct and specific — flag line numbers where relevant. Skip praise."
)

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger(__name__)


def get_source_name(repo: str) -> str:
    """Return the Jules source path for a repo."""
    return f"sources/github/{GITHUB_USER}/{repo}"


def infer_repo_from_git() -> str | None:
    """Try to read repo name from current directory's git remote."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True
        )
        url = result.stdout.strip()
        # Handle both SSH (git@github.com:user/repo.git) and HTTPS
        repo = url.rstrip("/").rstrip(".git").split("/")[-1]
        return repo
    except Exception:
        return None


def create_session(repo: str, prompt: str, branch: str = "main") -> str:
    """Submit a Jules session. Returns session ID."""
    payload = {
        "prompt": prompt,
        "sourceContext": {
            "source": get_source_name(repo),
            "githubRepoContext": {"startingBranch": branch},
        },
        "title": f"Review: {repo}",
        # Read-only review — don't auto-create a PR
    }
    resp = requests.post(f"{BASE_URL}/sessions", headers=HEADERS, json=payload)
    resp.raise_for_status()
    session = resp.json()
    session_id = session["name"].split("/")[-1]
    log.info("Session created: %s", session_id)
    return session_id


def poll_until_done(session_id: str) -> dict:
    """Poll session until state=COMPLETED. Returns the final session object + activities."""
    deadline = time.time() + POLL_TIMEOUT
    elapsed = 0

    while time.time() < deadline:
        # Check session state
        resp = requests.get(f"{BASE_URL}/sessions/{session_id}", headers=HEADERS)
        resp.raise_for_status()
        session = resp.json()
        state = session.get("state", "")

        print(f"[jules] {elapsed}s — state={state}", file=sys.stderr)

        if state == "COMPLETED":
            # Fetch activities now that we're done
            acts_resp = requests.get(
                f"{BASE_URL}/sessions/{session_id}/activities", headers=HEADERS
            )
            acts_resp.raise_for_status()
            session["activities"] = acts_resp.json().get("activities", [])
            return session

        if state == "FAILED":
            raise RuntimeError(f"Jules session failed: {session}")

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    raise TimeoutError(f"Jules session {session_id} did not complete within {POLL_TIMEOUT}s")


def extract_review(session: dict) -> str:
    """
    Extract Jules output from a completed session.

    Jules returns git patches (it fixes code, not just reviews). We pull:
    - progressUpdated descriptions (human-readable step summaries)
    - The final diff from sessionCompleted activity
    - The suggested commit message (summarises what changed and why)
    """
    progress_notes = []
    final_diff = None
    commit_message = None

    for act in session.get("activities", []):
        # Collect progress descriptions
        pu = act.get("progressUpdated", {})
        if pu.get("description"):
            progress_notes.append(pu["description"])

        # Find the final patch in sessionCompleted activity
        if "sessionCompleted" in act:
            for artifact in act.get("artifacts", []):
                cs = artifact.get("changeSet", {})
                gp = cs.get("gitPatch", {})
                if gp.get("unidiffPatch"):
                    final_diff = gp["unidiffPatch"]
                if gp.get("suggestedCommitMessage"):
                    commit_message = gp["suggestedCommitMessage"]

    parts = []
    if commit_message:
        parts.append(f"**Summary:**\n{commit_message}")
    if progress_notes:
        parts.append("**Steps taken:**\n" + "\n".join(f"- {n}" for n in progress_notes))
    if final_diff:
        parts.append(f"**Diff:**\n```diff\n{final_diff}\n```")

    return "\n\n".join(parts) if parts else "(no output extracted — session may have made no changes)"


def review(repo: str, prompt: str = DEFAULT_REVIEW_PROMPT, branch: str = "main") -> str:
    """Full review flow. Returns the review text."""
    print(f"[jules] submitting review for {repo} @ {branch}...", file=sys.stderr)
    session_id = create_session(repo, prompt, branch)
    print(f"[jules] session {session_id} — polling (may take several minutes)...", file=sys.stderr)
    session = poll_until_done(session_id)
    acts = session.get("activities", [])
    print(f"[jules] done ({len(acts)} activities)", file=sys.stderr)
    return extract_review(session)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Jules code review")
    parser.add_argument("--repo", help="GitHub repo name (e.g. ibkr-terminal). Autodetected from git if omitted.")
    parser.add_argument("--branch", default="main", help="Branch to review")
    parser.add_argument("--prompt", default=DEFAULT_REVIEW_PROMPT, help="Review prompt")
    args = parser.parse_args()

    repo = args.repo or infer_repo_from_git()
    if not repo:
        print("ERROR: --repo required (or run from a git repo directory)", file=sys.stderr)
        sys.exit(1)

    result = review(repo, args.prompt, args.branch)
    print(result)
