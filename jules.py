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

BASE_URL      = "https://jules.googleapis.com/v1alpha"
HEADERS       = {"X-Goog-Api-Key": JULES_API_KEY, "Content-Type": "application/json"}
POLL_INTERVAL = 8    # seconds between state polls
POLL_TIMEOUT  = 3600  # 60 min max wait

DEFAULT_REVIEW_PROMPT = (
    "Review this codebase for: (1) bugs or logic errors, (2) security vulnerabilities, "
    "(3) code quality issues, (4) anything that would break in production. "
    "Be direct and specific — flag line numbers where relevant. Skip praise."
)

PRESETS = {
    "security": (
        "Review this codebase strictly for security vulnerabilities: injection, "
        "auth bypasses, secret exposure, unsafe deserialization, SSRF, unsafe "
        "subprocess invocation, dependency CVEs. Skip everything else. Cite "
        "line numbers."
    ),
    "perf": (
        "Review this codebase strictly for performance issues: N+1 queries, "
        "unbounded loops, blocking I/O in hot paths, memory leaks, inefficient "
        "algorithms, excessive allocations. Skip everything else. Cite line numbers."
    ),
    "bugs": (
        "Review this codebase strictly for bugs and logic errors. Skip style, "
        "skip security, skip performance unless it causes incorrectness. Cite "
        "line numbers."
    ),
    "docs": (
        "Review this codebase's documentation: missing docstrings on public "
        "functions, outdated README claims, misleading comments, missing type "
        "hints. Do not change code logic."
    ),
}

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger(__name__)


def _raise_for_status(resp: requests.Response) -> None:
    """raise_for_status but include the response body in the error message."""
    if not resp.ok:
        raise requests.HTTPError(
            f"{resp.status_code} {resp.reason}: {resp.text}", response=resp
        )


def infer_repo_from_git() -> str | None:
    """Try to read repo name from current directory's git remote."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True,
        )
        url = result.stdout.strip().rstrip("/")
        # Handle both SSH (git@github.com:user/repo.git) and HTTPS
        name = url.split("/")[-1].removesuffix(".git")
        return name
    except Exception:
        return None


def create_session(repo: str, prompt: str, branch: str = "main") -> str:
    """Submit a Jules session. Returns session ID."""
    payload = {
        "prompt": prompt,
        "sourceContext": {
            "source": f"sources/github/{GITHUB_USER}/{repo}",
            "githubRepoContext": {"startingBranch": branch},
        },
        "title": f"Review: {repo}",
        # Read-only review — don't auto-create a PR
    }
    resp = requests.post(f"{BASE_URL}/sessions", headers=HEADERS, json=payload)
    _raise_for_status(resp)
    session_id = resp.json()["name"].split("/")[-1]
    log.info("Session created: %s", session_id)
    return session_id


def submit(repo: str, prompt: str = DEFAULT_REVIEW_PROMPT, branch: str = "main") -> str:
    """Submit a Jules session and return the session ID immediately. Does not poll."""
    return create_session(repo, prompt, branch)


def poll_until_done(session_id: str) -> dict:
    """Poll session until state=COMPLETED. Returns final session object with activities."""
    deadline = time.time() + POLL_TIMEOUT
    start = time.time()

    while time.time() < deadline:
        resp = requests.get(f"{BASE_URL}/sessions/{session_id}", headers=HEADERS)
        _raise_for_status(resp)
        session = resp.json()
        state = session.get("state", "")

        elapsed = int(time.time() - start)
        print(f"[jules] {elapsed}s — state={state}", file=sys.stderr)

        if state == "COMPLETED":
            acts_resp = requests.get(
                f"{BASE_URL}/sessions/{session_id}/activities", headers=HEADERS
            )
            _raise_for_status(acts_resp)
            session["activities"] = acts_resp.json().get("activities", [])
            return session

        if state == "FAILED":
            raise RuntimeError(f"Jules session failed: {session}")

        time.sleep(POLL_INTERVAL)

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
        pu = act.get("progressUpdated", {})
        if pu.get("description"):
            progress_notes.append(pu["description"])

        if "sessionCompleted" in act:
            for artifact in act.get("artifacts", []):
                gp = artifact.get("changeSet", {}).get("gitPatch", {})
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


def _extract_diff(session: dict) -> str | None:
    """Pull the unified diff from a completed Jules session. Returns None if no patch."""
    for act in session.get("activities", []):
        if "sessionCompleted" in act:
            for artifact in act.get("artifacts", []):
                gp = artifact.get("changeSet", {}).get("gitPatch", {})
                if gp.get("unidiffPatch"):
                    return gp["unidiffPatch"]
    return None


def _apply_diff(diff_text: str) -> None:
    """Pipe diff through `git apply` in CWD. Raises RuntimeError on failure."""
    result = subprocess.run(
        ["git", "apply"], input=diff_text, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git apply failed: {result.stderr}")


def _fetch_session(session_id: str) -> dict:
    """Poll + return the raw session dict (used by callers that need the unformatted data)."""
    return poll_until_done(session_id)


def fetch(session_id: str) -> str:
    """Poll an existing session until COMPLETED, then return formatted review text."""
    return extract_review(_fetch_session(session_id))


def post_to_discord(webhook_url: str, text: str) -> None:
    """Post review results to a Discord webhook, chunked at 2000 chars."""
    chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
    for chunk in chunks:
        resp = requests.post(webhook_url, json={"content": chunk})
        _raise_for_status(resp)


def review(repo: str, prompt: str = DEFAULT_REVIEW_PROMPT, branch: str = "main") -> str:
    """Full review flow. Returns the review text. Equivalent to fetch(submit(...))."""
    print(f"[jules] submitting review for {repo} @ {branch}...", file=sys.stderr)
    session_id = submit(repo, prompt, branch)
    print(f"[jules] session {session_id} — polling (may take several minutes)...", file=sys.stderr)
    return fetch(session_id)


def _resolve_prompt(args) -> str:
    if args.prompt:
        return args.prompt
    if args.preset:
        return PRESETS[args.preset]
    return DEFAULT_REVIEW_PROMPT


def _print_diff(diff: str) -> None:
    if not diff:
        return
    if diff.endswith("\n"):
        print(diff, end="")
    else:
        print(diff)


def _handle_session_output(session: dict, args) -> int:
    """Apply/format/markdown dispatch for a completed session. Returns exit code."""
    if args.apply:
        diff = _extract_diff(session)
        if not diff:
            print("ERROR: Jules returned no diff to apply", file=sys.stderr)
            return 1
        try:
            _apply_diff(diff)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    if args.format == "diff":
        diff = _extract_diff(session) or ""
        _print_diff(diff)
        if args.discord_channel:
            post_to_discord(args.discord_channel, diff)
        return 0

    result = extract_review(session)
    if args.discord_channel:
        post_to_discord(args.discord_channel, result)
    print(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code."""
    parser = argparse.ArgumentParser(description="Jules code review")
    parser.add_argument("--repo", help="GitHub repo name (e.g. ibkr-terminal). Autodetected from git if omitted.")
    parser.add_argument("--branch", default="main", help="Branch to review")
    parser.add_argument("--prompt", default=None, help="Review prompt (overrides default and --preset)")
    parser.add_argument("--preset", choices=list(PRESETS.keys()), help="Canned review prompt (mutually exclusive with --prompt)")
    parser.add_argument("--discord-channel", help="Discord webhook URL to post results to")
    parser.add_argument("--submit", action="store_true", help="Submit session and print ID without polling")
    parser.add_argument("--fetch", metavar="SESSION_ID", help="Poll and return review for an already-submitted session ID")
    parser.add_argument("--format", choices=["markdown", "diff"], default="markdown", help="Output format (default: markdown)")
    parser.add_argument("--apply", action="store_true", help="Pipe returned diff through `git apply` in CWD")
    args = parser.parse_args(argv)

    if args.preset and args.prompt:
        parser.error("--preset and --prompt are mutually exclusive")

    if args.submit and args.apply:
        parser.error("--submit and --apply are mutually exclusive (nothing to apply yet)")

    if args.fetch and (args.repo or args.prompt or args.preset or args.submit):
        parser.error("--fetch cannot be combined with --repo, --prompt, --preset, or --submit")

    if args.fetch:
        session = _fetch_session(args.fetch)
        return _handle_session_output(session, args)

    if args.submit:
        repo = args.repo or infer_repo_from_git()
        if not repo:
            print("ERROR: --repo required (or run from a git repo directory)", file=sys.stderr)
            return 1
        session_id = submit(repo, _resolve_prompt(args), args.branch)
        print(session_id)
        return 0

    repo = args.repo or infer_repo_from_git()
    if not repo:
        print("ERROR: --repo required (or run from a git repo directory)", file=sys.stderr)
        return 1

    prompt = _resolve_prompt(args)
    print(f"[jules] submitting review for {repo} @ {args.branch}...", file=sys.stderr)
    session_id = submit(repo, prompt, args.branch)
    print(f"[jules] session {session_id} — polling (may take several minutes)...", file=sys.stderr)
    session = _fetch_session(session_id)
    return _handle_session_output(session, args)


if __name__ == "__main__":
    sys.exit(main())
