# jules-review: async CLI + apply + diff-only + presets — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `review()` into submit/fetch, add `--apply`/`--format diff`/`--preset` CLI flags, add pytest coverage — zero regression on existing callers.

**Architecture:** Single-file refactor of `jules.py` plus new `tests/test_jules.py`. Public API grows by two functions (`submit`, `fetch`); existing `review()` becomes their composition. CLI gains five flags with mutual-exclusion rules. Tests mock at the `requests.post`/`requests.get` boundary — no real Jules API calls.

**Tech Stack:** Python 3.12, requests, python-dotenv, pytest.

---

## File structure

- **Modify:** `~/repos/jules-review/jules.py` — all new API + CLI logic
- **Create:** `~/repos/jules-review/tests/__init__.py` — empty
- **Create:** `~/repos/jules-review/tests/conftest.py` — shared fixtures (fake session dicts, JULES env vars)
- **Create:** `~/repos/jules-review/tests/test_jules.py` — all tests
- **Create:** `~/repos/jules-review/pytest.ini` — pytest config
- **Modify:** `~/repos/jules-review/requirements.txt` — add `pytest==8.3.4`

---

## Task 1: Test scaffolding

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `pytest.ini`
- Modify: `requirements.txt`

- [ ] **Step 1: Create empty `tests/__init__.py`**

```bash
touch tests/__init__.py
```

- [ ] **Step 2: Write `pytest.ini`**

```ini
[pytest]
testpaths = tests
```

- [ ] **Step 3: Add `pytest` to `requirements.txt`**

Append one line to end of file:

```
pytest==8.3.4
```

- [ ] **Step 4: Install pytest**

```bash
cd ~/repos/jules-review && source venv/bin/activate && pip install pytest==8.3.4 && pip freeze > requirements.txt
```

- [ ] **Step 5: Write `conftest.py` with shared fixtures**

```python
"""Shared pytest fixtures for jules-review tests."""
import pytest


@pytest.fixture(autouse=True)
def jules_env(monkeypatch):
    """Every test runs with dummy Jules credentials so jules.py import doesn't fail."""
    monkeypatch.setenv("JULES_API_KEY", "test-key")
    monkeypatch.setenv("JULES_GITHUB_USER", "test-user")


@pytest.fixture
def completed_session_with_diff():
    """A Jules session response with a non-empty diff."""
    return {
        "name": "sessions/abc123",
        "state": "COMPLETED",
        "activities": [
            {"progressUpdated": {"description": "Cloned repo"}},
            {"progressUpdated": {"description": "Analyzed code"}},
            {
                "sessionCompleted": {},
                "artifacts": [
                    {
                        "changeSet": {
                            "gitPatch": {
                                "unidiffPatch": (
                                    "diff --git a/foo.py b/foo.py\n"
                                    "--- a/foo.py\n"
                                    "+++ b/foo.py\n"
                                    "@@ -1,1 +1,1 @@\n"
                                    "-x = 1\n"
                                    "+x = 2\n"
                                ),
                                "suggestedCommitMessage": "fix: bump x",
                            }
                        }
                    }
                ],
            },
        ],
    }


@pytest.fixture
def completed_session_no_diff():
    """A Jules session that completed but produced no code changes."""
    return {
        "name": "sessions/abc123",
        "state": "COMPLETED",
        "activities": [
            {"progressUpdated": {"description": "Cloned repo"}},
            {"sessionCompleted": {}, "artifacts": []},
        ],
    }


@pytest.fixture
def failed_session():
    return {"name": "sessions/xyz", "state": "FAILED", "error": "invalid repo"}


@pytest.fixture
def in_progress_session():
    return {"name": "sessions/abc123", "state": "IN_PROGRESS"}


class FakeResponse:
    """Minimal stand-in for requests.Response."""
    def __init__(self, json_body, status_code=200):
        self._json = json_body
        self.status_code = status_code
        self.reason = "OK" if status_code == 200 else "Error"
        self.text = str(json_body)
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._json


@pytest.fixture
def fake_response():
    return FakeResponse
```

- [ ] **Step 6: Verify pytest runs (no tests yet, should say "no tests collected")**

```bash
cd ~/repos/jules-review && source venv/bin/activate && pytest tests/ -q
```

Expected: `no tests ran` or `0 items collected` — pytest is wired up.

- [ ] **Step 7: Commit**

```bash
cd ~/repos/jules-review && git add tests/__init__.py tests/conftest.py pytest.ini requirements.txt && git commit -m "test: add pytest scaffolding and shared fixtures"
```

---

## Task 2: `submit()` function — non-blocking session creation

**Files:**
- Modify: `jules.py` — add `submit()` function
- Modify: `tests/test_jules.py` — test for `submit()`

- [ ] **Step 1: Write failing test for `submit()`**

Create `tests/test_jules.py`:

```python
"""Tests for jules.py."""
from unittest.mock import patch
import jules


def test_submit_returns_session_id(fake_response):
    """submit() posts to /sessions and returns parsed ID without polling."""
    response_body = {"name": "sessions/abc123", "state": "IN_PROGRESS"}

    with patch("jules.requests.post", return_value=fake_response(response_body)) as mock_post, \
         patch("jules.requests.get") as mock_get:
        session_id = jules.submit("my-repo", "review for bugs", branch="main")

    assert session_id == "abc123"
    mock_post.assert_called_once()
    mock_get.assert_not_called()  # key guarantee: no polling
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/repos/jules-review && source venv/bin/activate && pytest tests/test_jules.py::test_submit_returns_session_id -v
```

Expected: FAIL with `AttributeError: module 'jules' has no attribute 'submit'`.

- [ ] **Step 3: Add `submit()` to `jules.py`**

Add this function in `jules.py` directly after the existing `create_session()` function (around line 85). `submit()` is a thin public wrapper around `create_session()`:

```python
def submit(repo: str, prompt: str = DEFAULT_REVIEW_PROMPT, branch: str = "main") -> str:
    """Submit a Jules session and return the session ID immediately. Does not poll."""
    return create_session(repo, prompt, branch)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_jules.py::test_submit_returns_session_id -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jules.py tests/test_jules.py && git commit -m "feat: add submit() for non-blocking session creation"
```

---

## Task 3: `fetch()` function — polls existing session ID

**Files:**
- Modify: `jules.py` — add `fetch()` function
- Modify: `tests/test_jules.py`

- [ ] **Step 1: Write failing test for `fetch()` on completed session**

Append to `tests/test_jules.py`:

```python
def test_fetch_polls_until_completed(fake_response, completed_session_with_diff):
    """fetch() polls GET /sessions/{id} until state=COMPLETED, then returns review text."""
    activities_response = {"activities": completed_session_with_diff["activities"]}

    responses = [
        fake_response({"state": "IN_PROGRESS"}),
        fake_response({"state": "IN_PROGRESS"}),
        fake_response(completed_session_with_diff),  # session GET when completed
        fake_response(activities_response),  # activities GET
    ]

    with patch("jules.requests.get", side_effect=responses), \
         patch("jules.time.sleep"):  # skip actual waits
        result = jules.fetch("abc123")

    assert "**Summary:**" in result
    assert "fix: bump x" in result
    assert "**Diff:**" in result
    assert "x = 2" in result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_jules.py::test_fetch_polls_until_completed -v
```

Expected: FAIL with `AttributeError: module 'jules' has no attribute 'fetch'`.

- [ ] **Step 3: Add `fetch()` to `jules.py`**

Add this function directly after the existing `extract_review()` function (around line 151). `fetch()` composes `poll_until_done()` + `extract_review()`:

```python
def fetch(session_id: str) -> str:
    """Poll an existing session until COMPLETED, then return formatted review text."""
    session = poll_until_done(session_id)
    return extract_review(session)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_jules.py::test_fetch_polls_until_completed -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jules.py tests/test_jules.py && git commit -m "feat: add fetch() to poll + extract review from existing session"
```

---

## Task 4: Refactor `review()` to use `submit` + `fetch`

**Files:**
- Modify: `jules.py` — make `review()` compose `submit` + `fetch`
- Modify: `tests/test_jules.py`

- [ ] **Step 1: Write failing test that `review()` equals `submit()` + `fetch()`**

Append to `tests/test_jules.py`:

```python
def test_review_is_submit_plus_fetch(fake_response, completed_session_with_diff):
    """review() output must be identical to fetch(submit()). Zero regression."""
    post_response = fake_response({"name": "sessions/abc123"})
    activities_response = {"activities": completed_session_with_diff["activities"]}
    get_responses = [
        fake_response(completed_session_with_diff),
        fake_response(activities_response),
    ]

    with patch("jules.requests.post", return_value=post_response), \
         patch("jules.requests.get", side_effect=get_responses), \
         patch("jules.time.sleep"):
        result = jules.review("my-repo", "review for bugs", branch="main")

    # Same markdown output the existing extract_review produces
    assert "**Summary:**" in result
    assert "fix: bump x" in result
    assert "**Diff:**" in result
```

- [ ] **Step 2: Run test — should pass immediately**

```bash
pytest tests/test_jules.py::test_review_is_submit_plus_fetch -v
```

Expected: PASS (existing `review()` already behaves correctly; this test locks in the contract).

- [ ] **Step 3: Refactor `review()` to literally call `submit` + `fetch`**

In `jules.py`, replace the current `review()` function body (around lines 162-170) with:

```python
def review(repo: str, prompt: str = DEFAULT_REVIEW_PROMPT, branch: str = "main") -> str:
    """Full review flow. Returns the review text. Equivalent to fetch(submit(...))."""
    print(f"[jules] submitting review for {repo} @ {branch}...", file=sys.stderr)
    session_id = submit(repo, prompt, branch)
    print(f"[jules] session {session_id} — polling (may take several minutes)...", file=sys.stderr)
    return fetch(session_id)
```

- [ ] **Step 4: Run the full test suite to confirm no regressions**

```bash
pytest tests/ -v
```

Expected: all tests pass (test_submit_returns_session_id, test_fetch_polls_until_completed, test_review_is_submit_plus_fetch).

- [ ] **Step 5: Commit**

```bash
git add jules.py tests/test_jules.py && git commit -m "refactor: review() composes submit() + fetch()"
```

---

## Task 5: `fetch()` error cases — FAILED and timeout

**Files:**
- Modify: `tests/test_jules.py`

- [ ] **Step 1: Write failing tests for error paths**

Append to `tests/test_jules.py`:

```python
import pytest


def test_fetch_raises_on_failed_session(fake_response, failed_session):
    """fetch() raises RuntimeError if session state becomes FAILED."""
    with patch("jules.requests.get", return_value=fake_response(failed_session)), \
         patch("jules.time.sleep"):
        with pytest.raises(RuntimeError, match="Jules session failed"):
            jules.fetch("xyz")


def test_fetch_raises_on_timeout(fake_response, in_progress_session, monkeypatch):
    """fetch() raises TimeoutError if session never completes within POLL_TIMEOUT."""
    monkeypatch.setattr("jules.POLL_TIMEOUT", 0)  # immediate timeout

    with patch("jules.requests.get", return_value=fake_response(in_progress_session)), \
         patch("jules.time.sleep"):
        with pytest.raises(TimeoutError, match="did not complete"):
            jules.fetch("abc123")
```

- [ ] **Step 2: Run tests — should pass (existing `poll_until_done` handles both)**

```bash
pytest tests/test_jules.py::test_fetch_raises_on_failed_session tests/test_jules.py::test_fetch_raises_on_timeout -v
```

Expected: PASS (these lock in existing behavior via the new `fetch()` entry point).

- [ ] **Step 3: Commit**

```bash
git add tests/test_jules.py && git commit -m "test: lock in fetch() error paths (FAILED, timeout)"
```

---

## Task 6: `_extract_diff()` helper

**Files:**
- Modify: `jules.py` — add `_extract_diff()` helper
- Modify: `tests/test_jules.py`

- [ ] **Step 1: Write failing test for `_extract_diff()` with diff**

Append to `tests/test_jules.py`:

```python
def test_extract_diff_returns_patch(completed_session_with_diff):
    """_extract_diff() pulls gitPatch.unidiffPatch from a completed session."""
    diff = jules._extract_diff(completed_session_with_diff)
    assert diff is not None
    assert "diff --git a/foo.py b/foo.py" in diff
    assert "x = 2" in diff


def test_extract_diff_returns_none_for_no_changes(completed_session_no_diff):
    """_extract_diff() returns None when the session made no code changes."""
    assert jules._extract_diff(completed_session_no_diff) is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_jules.py::test_extract_diff_returns_patch tests/test_jules.py::test_extract_diff_returns_none_for_no_changes -v
```

Expected: FAIL with `AttributeError: module 'jules' has no attribute '_extract_diff'`.

- [ ] **Step 3: Add `_extract_diff()` to `jules.py`**

Add this helper directly after `extract_review()` (around line 152):

```python
def _extract_diff(session: dict) -> str | None:
    """Pull the unified diff from a completed Jules session. Returns None if no patch."""
    for act in session.get("activities", []):
        if "sessionCompleted" in act:
            for artifact in act.get("artifacts", []):
                gp = artifact.get("changeSet", {}).get("gitPatch", {})
                if gp.get("unidiffPatch"):
                    return gp["unidiffPatch"]
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_jules.py::test_extract_diff_returns_patch tests/test_jules.py::test_extract_diff_returns_none_for_no_changes -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jules.py tests/test_jules.py && git commit -m "feat: add _extract_diff() helper for diff-only and apply flows"
```

---

## Task 7: Preset prompts — `PRESETS` dict

**Files:**
- Modify: `jules.py` — add `PRESETS` dict
- Modify: `tests/test_jules.py`

- [ ] **Step 1: Write failing test that PRESETS exists with expected keys**

Append to `tests/test_jules.py`:

```python
def test_presets_available():
    """PRESETS dict contains all four documented presets."""
    assert set(jules.PRESETS.keys()) == {"security", "perf", "bugs", "docs"}
    assert "security" in jules.PRESETS["security"].lower()
    assert "performance" in jules.PRESETS["perf"].lower()
    assert "bugs" in jules.PRESETS["bugs"].lower()
    assert "documentation" in jules.PRESETS["docs"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_jules.py::test_presets_available -v
```

Expected: FAIL with `AttributeError: module 'jules' has no attribute 'PRESETS'`.

- [ ] **Step 3: Add `PRESETS` to `jules.py`**

Add this dict directly after `DEFAULT_REVIEW_PROMPT` (around line 41):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_jules.py::test_presets_available -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jules.py tests/test_jules.py && git commit -m "feat: add PRESETS dict for canned review prompts"
```

---

## Task 8: `_apply_diff()` helper

**Files:**
- Modify: `jules.py` — add `_apply_diff()`
- Modify: `tests/test_jules.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_jules.py`:

```python
def test_apply_diff_invokes_git_apply():
    """_apply_diff() pipes the diff text through `git apply` via subprocess."""
    fake_result = type("R", (), {"returncode": 0, "stderr": ""})()

    with patch("jules.subprocess.run", return_value=fake_result) as mock_run:
        jules._apply_diff("diff --git a/x b/x\n")

    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert args[0] == ["git", "apply"]
    assert kwargs["input"] == "diff --git a/x b/x\n"
    assert kwargs["text"] is True


def test_apply_diff_raises_on_nonzero_exit():
    """_apply_diff() raises RuntimeError with stderr when git apply fails."""
    fake_result = type("R", (), {"returncode": 1, "stderr": "patch does not apply"})()

    with patch("jules.subprocess.run", return_value=fake_result):
        with pytest.raises(RuntimeError, match="git apply failed: patch does not apply"):
            jules._apply_diff("bad diff")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_jules.py::test_apply_diff_invokes_git_apply tests/test_jules.py::test_apply_diff_raises_on_nonzero_exit -v
```

Expected: FAIL with `AttributeError: module 'jules' has no attribute '_apply_diff'`.

- [ ] **Step 3: Add `_apply_diff()` to `jules.py`**

Add directly after `_extract_diff()` (around line 163):

```python
def _apply_diff(diff_text: str) -> None:
    """Pipe diff through `git apply` in CWD. Raises RuntimeError on failure."""
    result = subprocess.run(
        ["git", "apply"], input=diff_text, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git apply failed: {result.stderr}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_jules.py::test_apply_diff_invokes_git_apply tests/test_jules.py::test_apply_diff_raises_on_nonzero_exit -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jules.py tests/test_jules.py && git commit -m "feat: add _apply_diff() helper to pipe diffs through git apply"
```

---

## Task 9: Refactor CLI into `main(argv)` + `--submit` flag

**Files:**
- Modify: `jules.py` — CLI block becomes `main()` function
- Modify: `tests/test_jules.py`

- [ ] **Step 1: Write failing test for `main()` with `--submit`**

Append to `tests/test_jules.py`:

```python
def test_main_submit_prints_session_id(fake_response, capsys):
    """main(['--repo', 'my-repo', '--submit']) prints session ID, does not poll."""
    post_response = fake_response({"name": "sessions/abc123"})

    with patch("jules.requests.post", return_value=post_response), \
         patch("jules.requests.get") as mock_get:
        exit_code = jules.main(["--repo", "my-repo", "--submit"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "abc123"
    mock_get.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_jules.py::test_main_submit_prints_session_id -v
```

Expected: FAIL with `AttributeError: module 'jules' has no attribute 'main'`.

- [ ] **Step 3: Refactor CLI into `main(argv)` and add `--submit`**

Replace the existing `if __name__ == "__main__":` block (around lines 173-189) with a `main()` function followed by a thin `__main__` guard:

```python
def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code."""
    parser = argparse.ArgumentParser(description="Jules code review")
    parser.add_argument("--repo", help="GitHub repo name (e.g. ibkr-terminal). Autodetected from git if omitted.")
    parser.add_argument("--branch", default="main", help="Branch to review")
    parser.add_argument("--prompt", default=None, help="Review prompt (overrides default and --preset)")
    parser.add_argument("--discord-channel", help="Discord webhook URL to post results to")
    parser.add_argument("--submit", action="store_true", help="Submit session and print ID without polling")
    args = parser.parse_args(argv)

    if args.submit:
        repo = args.repo or infer_repo_from_git()
        if not repo:
            print("ERROR: --repo required (or run from a git repo directory)", file=sys.stderr)
            return 1
        prompt = args.prompt or DEFAULT_REVIEW_PROMPT
        session_id = submit(repo, prompt, args.branch)
        print(session_id)
        return 0

    # Default path: full review
    repo = args.repo or infer_repo_from_git()
    if not repo:
        print("ERROR: --repo required (or run from a git repo directory)", file=sys.stderr)
        return 1
    prompt = args.prompt or DEFAULT_REVIEW_PROMPT
    result = review(repo, prompt, args.branch)
    if args.discord_channel:
        post_to_discord(args.discord_channel, result)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_jules.py::test_main_submit_prints_session_id -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite to confirm nothing broke**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add jules.py tests/test_jules.py && git commit -m "feat: --submit flag + refactor CLI into main(argv)"
```

---

## Task 10: CLI — `--fetch SESSION_ID`

**Files:**
- Modify: `jules.py` — add `--fetch`
- Modify: `tests/test_jules.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_jules.py`:

```python
def test_main_fetch_prints_review(fake_response, completed_session_with_diff, capsys):
    """main(['--fetch', 'abc123']) polls and prints review — no --repo needed."""
    activities_response = {"activities": completed_session_with_diff["activities"]}
    get_responses = [
        fake_response(completed_session_with_diff),
        fake_response(activities_response),
    ]

    with patch("jules.requests.get", side_effect=get_responses), \
         patch("jules.requests.post") as mock_post, \
         patch("jules.time.sleep"):
        exit_code = jules.main(["--fetch", "abc123"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "**Summary:**" in captured.out
    assert "x = 2" in captured.out
    mock_post.assert_not_called()  # --fetch must not create a new session


def test_main_fetch_and_repo_mutually_exclusive(capsys):
    """--fetch ID --repo X is an error."""
    with pytest.raises(SystemExit) as exc:
        jules.main(["--fetch", "abc123", "--repo", "my-repo"])
    assert exc.value.code != 0
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_jules.py::test_main_fetch_prints_review tests/test_jules.py::test_main_fetch_and_repo_mutually_exclusive -v
```

Expected: FAIL (no `--fetch` arg yet).

- [ ] **Step 3: Add `--fetch` to `main()`**

In `main()`, add the argparse entry and early-return branch. Replace the existing argparse setup + body:

```python
def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code."""
    parser = argparse.ArgumentParser(description="Jules code review")
    parser.add_argument("--repo", help="GitHub repo name (e.g. ibkr-terminal). Autodetected from git if omitted.")
    parser.add_argument("--branch", default="main", help="Branch to review")
    parser.add_argument("--prompt", default=None, help="Review prompt (overrides default and --preset)")
    parser.add_argument("--discord-channel", help="Discord webhook URL to post results to")
    parser.add_argument("--submit", action="store_true", help="Submit session and print ID without polling")
    parser.add_argument("--fetch", metavar="SESSION_ID", help="Poll and return review for an already-submitted session ID")
    args = parser.parse_args(argv)

    # --fetch is mutually exclusive with every submission flag
    if args.fetch and (args.repo or args.prompt or args.submit):
        parser.error("--fetch cannot be combined with --repo, --prompt, or --submit")

    if args.fetch:
        result = fetch(args.fetch)
        if args.discord_channel:
            post_to_discord(args.discord_channel, result)
        print(result)
        return 0

    if args.submit:
        repo = args.repo or infer_repo_from_git()
        if not repo:
            print("ERROR: --repo required (or run from a git repo directory)", file=sys.stderr)
            return 1
        prompt = args.prompt or DEFAULT_REVIEW_PROMPT
        session_id = submit(repo, prompt, args.branch)
        print(session_id)
        return 0

    # Default path: full review
    repo = args.repo or infer_repo_from_git()
    if not repo:
        print("ERROR: --repo required (or run from a git repo directory)", file=sys.stderr)
        return 1
    prompt = args.prompt or DEFAULT_REVIEW_PROMPT
    result = review(repo, prompt, args.branch)
    if args.discord_channel:
        post_to_discord(args.discord_channel, result)
    print(result)
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_jules.py::test_main_fetch_prints_review tests/test_jules.py::test_main_fetch_and_repo_mutually_exclusive -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add jules.py tests/test_jules.py && git commit -m "feat: --fetch SESSION_ID flag for polling existing sessions"
```

---

## Task 11: CLI — `--preset`

**Files:**
- Modify: `jules.py` — wire `--preset` into argparse
- Modify: `tests/test_jules.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_jules.py`:

```python
def test_main_preset_security_uses_security_prompt(fake_response, completed_session_with_diff):
    """--preset security passes the security prompt to create_session."""
    post_response = fake_response({"name": "sessions/abc123"})
    activities_response = {"activities": completed_session_with_diff["activities"]}
    get_responses = [
        fake_response(completed_session_with_diff),
        fake_response(activities_response),
    ]

    with patch("jules.requests.post", return_value=post_response) as mock_post, \
         patch("jules.requests.get", side_effect=get_responses), \
         patch("jules.time.sleep"):
        jules.main(["--repo", "my-repo", "--preset", "security"])

    # Verify the security preset prompt was sent in the POST payload
    called_json = mock_post.call_args.kwargs["json"]
    assert "security" in called_json["prompt"].lower()
    assert "injection" in called_json["prompt"].lower()


def test_main_preset_and_prompt_mutually_exclusive():
    """--preset X --prompt 'custom' is an error."""
    with pytest.raises(SystemExit) as exc:
        jules.main(["--repo", "my-repo", "--preset", "bugs", "--prompt", "custom"])
    assert exc.value.code != 0
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_jules.py::test_main_preset_security_uses_security_prompt tests/test_jules.py::test_main_preset_and_prompt_mutually_exclusive -v
```

Expected: FAIL with `argparse` error (no `--preset`).

- [ ] **Step 3: Add `--preset` to `main()`**

In `main()`, add the argparse entry and prompt resolution. Update the argparse section and prompt resolution logic:

```python
    parser.add_argument(
        "--preset",
        choices=list(PRESETS.keys()),
        help="Canned review prompt (mutually exclusive with --prompt)",
    )
```

Add this right after `args = parser.parse_args(argv)` and before the `--fetch` check:

```python
    if args.preset and args.prompt:
        parser.error("--preset and --prompt are mutually exclusive")
```

Replace every `prompt = args.prompt or DEFAULT_REVIEW_PROMPT` line in `main()` with:

```python
        prompt = args.prompt or (PRESETS[args.preset] if args.preset else DEFAULT_REVIEW_PROMPT)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_jules.py::test_main_preset_security_uses_security_prompt tests/test_jules.py::test_main_preset_and_prompt_mutually_exclusive -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add jules.py tests/test_jules.py && git commit -m "feat: --preset {security,perf,bugs,docs} for canned review prompts"
```

---

## Task 12: CLI — `--format diff`

**Files:**
- Modify: `jules.py`
- Modify: `tests/test_jules.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_jules.py`:

```python
def test_main_format_diff_prints_only_patch(fake_response, completed_session_with_diff, capsys):
    """--format diff prints ONLY the unified diff — no summary, no markdown fences."""
    post_response = fake_response({"name": "sessions/abc123"})
    activities_response = {"activities": completed_session_with_diff["activities"]}
    get_responses = [
        fake_response(completed_session_with_diff),
        fake_response(activities_response),
    ]

    with patch("jules.requests.post", return_value=post_response), \
         patch("jules.requests.get", side_effect=get_responses), \
         patch("jules.time.sleep"):
        exit_code = jules.main(["--repo", "my-repo", "--format", "diff"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "**Summary:**" not in captured.out
    assert "```diff" not in captured.out
    assert "diff --git a/foo.py b/foo.py" in captured.out
    assert "x = 2" in captured.out


def test_main_format_diff_empty_when_no_changes(fake_response, completed_session_no_diff, capsys):
    """--format diff with no-code-change session → empty stdout, exit 0."""
    post_response = fake_response({"name": "sessions/abc123"})
    activities_response = {"activities": completed_session_no_diff["activities"]}
    get_responses = [
        fake_response(completed_session_no_diff),
        fake_response(activities_response),
    ]

    with patch("jules.requests.post", return_value=post_response), \
         patch("jules.requests.get", side_effect=get_responses), \
         patch("jules.time.sleep"):
        exit_code = jules.main(["--repo", "my-repo", "--format", "diff"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "" or captured.out.strip() == ""
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_jules.py::test_main_format_diff_prints_only_patch tests/test_jules.py::test_main_format_diff_empty_when_no_changes -v
```

Expected: FAIL (no `--format`).

- [ ] **Step 3: Add `--format` and re-route output**

In `main()`, add to argparse:

```python
    parser.add_argument(
        "--format",
        choices=["markdown", "diff"],
        default="markdown",
        help="Output format (default: markdown)",
    )
```

For `--format diff` we need the raw session so we can call `_extract_diff`. Since `review()` wraps `submit + fetch` and `fetch()` returns formatted text, introduce a new internal helper `_fetch_session(id) -> dict` that `fetch()` uses under the hood:

Add directly before `fetch()` in `jules.py`:

```python
def _fetch_session(session_id: str) -> dict:
    """Poll + return the raw session dict (used by callers that need the unformatted data)."""
    return poll_until_done(session_id)
```

Rewrite `fetch()` as:

```python
def fetch(session_id: str) -> str:
    """Poll an existing session until COMPLETED, then return formatted review text."""
    return extract_review(_fetch_session(session_id))
```

Now update the default (non-submit, non-fetch) path in `main()`:

```python
    # Default path: full review
    repo = args.repo or infer_repo_from_git()
    if not repo:
        print("ERROR: --repo required (or run from a git repo directory)", file=sys.stderr)
        return 1
    prompt = args.prompt or (PRESETS[args.preset] if args.preset else DEFAULT_REVIEW_PROMPT)

    print(f"[jules] submitting review for {repo} @ {args.branch}...", file=sys.stderr)
    session_id = submit(repo, prompt, args.branch)
    print(f"[jules] session {session_id} — polling (may take several minutes)...", file=sys.stderr)
    session = _fetch_session(session_id)

    if args.format == "diff":
        diff = _extract_diff(session) or ""
        print(diff, end="" if diff.endswith("\n") else "\n" if diff else "")
        if args.discord_channel:
            post_to_discord(args.discord_channel, diff)
        return 0

    result = extract_review(session)
    if args.discord_channel:
        post_to_discord(args.discord_channel, result)
    print(result)
    return 0
```

Also update the `--fetch` path:

```python
    if args.fetch:
        session = _fetch_session(args.fetch)
        if args.format == "diff":
            diff = _extract_diff(session) or ""
            print(diff, end="" if diff.endswith("\n") else "\n" if diff else "")
            if args.discord_channel:
                post_to_discord(args.discord_channel, diff)
            return 0
        result = extract_review(session)
        if args.discord_channel:
            post_to_discord(args.discord_channel, result)
        print(result)
        return 0
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_jules.py::test_main_format_diff_prints_only_patch tests/test_jules.py::test_main_format_diff_empty_when_no_changes -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add jules.py tests/test_jules.py && git commit -m "feat: --format diff for machine-readable output"
```

---

## Task 13: CLI — `--apply`

**Files:**
- Modify: `jules.py`
- Modify: `tests/test_jules.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_jules.py`:

```python
def test_main_apply_invokes_git_apply(fake_response, completed_session_with_diff, capsys):
    """--apply pipes the returned diff through git apply. Markdown still prints."""
    post_response = fake_response({"name": "sessions/abc123"})
    activities_response = {"activities": completed_session_with_diff["activities"]}
    get_responses = [
        fake_response(completed_session_with_diff),
        fake_response(activities_response),
    ]
    fake_git_result = type("R", (), {"returncode": 0, "stderr": ""})()

    with patch("jules.requests.post", return_value=post_response), \
         patch("jules.requests.get", side_effect=get_responses), \
         patch("jules.time.sleep"), \
         patch("jules.subprocess.run", return_value=fake_git_result) as mock_run:
        exit_code = jules.main(["--repo", "my-repo", "--apply"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "**Summary:**" in captured.out  # still prints markdown

    # git apply was invoked with the unified diff on stdin
    git_apply_calls = [c for c in mock_run.call_args_list if c.args[0] == ["git", "apply"]]
    assert len(git_apply_calls) == 1
    assert "x = 2" in git_apply_calls[0].kwargs["input"]


def test_main_apply_exits_nonzero_on_git_apply_failure(fake_response, completed_session_with_diff, capsys):
    """--apply: git apply returns nonzero → CLI exits 1, error on stderr."""
    post_response = fake_response({"name": "sessions/abc123"})
    activities_response = {"activities": completed_session_with_diff["activities"]}
    get_responses = [
        fake_response(completed_session_with_diff),
        fake_response(activities_response),
    ]
    fake_git_result = type("R", (), {"returncode": 1, "stderr": "patch does not apply"})()

    with patch("jules.requests.post", return_value=post_response), \
         patch("jules.requests.get", side_effect=get_responses), \
         patch("jules.time.sleep"), \
         patch("jules.subprocess.run", return_value=fake_git_result):
        exit_code = jules.main(["--repo", "my-repo", "--apply"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "patch does not apply" in captured.err


def test_main_apply_exits_nonzero_when_no_diff(fake_response, completed_session_no_diff, capsys):
    """--apply: session returned no diff → exit 1, clear message."""
    post_response = fake_response({"name": "sessions/abc123"})
    activities_response = {"activities": completed_session_no_diff["activities"]}
    get_responses = [
        fake_response(completed_session_no_diff),
        fake_response(activities_response),
    ]

    with patch("jules.requests.post", return_value=post_response), \
         patch("jules.requests.get", side_effect=get_responses), \
         patch("jules.time.sleep"), \
         patch("jules.subprocess.run") as mock_run:
        exit_code = jules.main(["--repo", "my-repo", "--apply"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "no diff" in captured.err.lower()
    mock_run.assert_not_called()


def test_main_submit_rejects_apply():
    """--submit --apply is an error (nothing to apply yet)."""
    with pytest.raises(SystemExit) as exc:
        jules.main(["--repo", "my-repo", "--submit", "--apply"])
    assert exc.value.code != 0
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_jules.py::test_main_apply_invokes_git_apply tests/test_jules.py::test_main_apply_exits_nonzero_on_git_apply_failure tests/test_jules.py::test_main_apply_exits_nonzero_when_no_diff tests/test_jules.py::test_main_submit_rejects_apply -v
```

Expected: FAIL (no `--apply`).

- [ ] **Step 3: Add `--apply` to `main()`**

In argparse:

```python
    parser.add_argument("--apply", action="store_true", help="Pipe returned diff through `git apply` in CWD")
```

After `args = parser.parse_args(argv)` and the preset mutex check, add:

```python
    if args.submit and args.apply:
        parser.error("--submit and --apply are mutually exclusive (nothing to apply yet)")
```

In both the `--fetch` branch and default branch, after `session = _fetch_session(...)`, handle `--apply` BEFORE the format/markdown branch. Replace the default-branch body (from `session = _fetch_session(...)` onward) with:

```python
    session = _fetch_session(session_id)

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
        print(diff, end="" if diff.endswith("\n") else "\n" if diff else "")
        if args.discord_channel:
            post_to_discord(args.discord_channel, diff)
        return 0

    result = extract_review(session)
    if args.discord_channel:
        post_to_discord(args.discord_channel, result)
    print(result)
    return 0
```

Similarly replace the `--fetch` body (from `session = _fetch_session(args.fetch)` onward) with the same block above. The full `--fetch` branch becomes:

```python
    if args.fetch:
        session = _fetch_session(args.fetch)

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
            print(diff, end="" if diff.endswith("\n") else "\n" if diff else "")
            if args.discord_channel:
                post_to_discord(args.discord_channel, diff)
            return 0

        result = extract_review(session)
        if args.discord_channel:
            post_to_discord(args.discord_channel, result)
        print(result)
        return 0
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_jules.py::test_main_apply_invokes_git_apply tests/test_jules.py::test_main_apply_exits_nonzero_on_git_apply_failure tests/test_jules.py::test_main_apply_exits_nonzero_when_no_diff tests/test_jules.py::test_main_submit_rejects_apply -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -v
```

Expected: all tests pass (should be 18+ tests now).

- [ ] **Step 6: Commit**

```bash
git add jules.py tests/test_jules.py && git commit -m "feat: --apply flag pipes returned diff through git apply"
```

---

## Task 14: README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read existing README**

```bash
cat ~/repos/jules-review/README.md
```

- [ ] **Step 2: Append new sections to README**

Use the Edit tool to append the following to the END of `~/repos/jules-review/README.md` (do not disturb existing content):

````markdown

## Async workflow

For bots and scripts that can't block for 10-30 min:

```bash
# Submit and get session ID immediately
SESSION=$(python jules.py --repo my-repo --submit)

# Later (from anywhere)
python jules.py --fetch "$SESSION"
```

## Apply a patch directly

```bash
# Review + apply diff to CWD in one shot
python jules.py --repo my-repo --apply

# Or pipe machine-readable output
python jules.py --repo my-repo --format diff | git apply
```

## Preset prompts

```bash
python jules.py --repo my-repo --preset security
python jules.py --repo my-repo --preset perf
python jules.py --repo my-repo --preset bugs
python jules.py --repo my-repo --preset docs
```

## CLI reference

| Flag | Purpose |
|---|---|
| `--repo NAME` | GitHub repo under `$JULES_GITHUB_USER`. Autodetected from `git remote` if in a repo. |
| `--branch NAME` | Branch to review (default `main`). |
| `--prompt TEXT` | Custom review prompt (overrides `--preset` and default). |
| `--preset NAME` | Canned prompt: `security`, `perf`, `bugs`, `docs`. |
| `--submit` | Submit session, print ID, exit. No polling. |
| `--fetch SESSION_ID` | Poll an existing session and print review. |
| `--format {markdown,diff}` | Output format (default `markdown`). |
| `--apply` | After review, pipe the diff into `git apply` in CWD. |
| `--discord-channel URL` | Also post result to a Discord webhook. |

## Tests

```bash
source venv/bin/activate
pytest tests/
```
````

- [ ] **Step 3: Verify README renders reasonably**

```bash
cd ~/repos/jules-review && head -80 README.md
```

Expected: new sections visible in the file.

- [ ] **Step 4: Commit**

```bash
git add README.md && git commit -m "docs: document async workflow, --apply, --format, --preset"
```

---

## Task 15: Final integration — manual smoke test

**Files:** None (manual verification)

- [ ] **Step 1: Run full test suite**

```bash
cd ~/repos/jules-review && source venv/bin/activate && pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 2: Verify default CLI still works (dry check, no real API)**

```bash
python jules.py --help
```

Expected: argparse help text includes all new flags: `--submit`, `--fetch`, `--format`, `--apply`, `--preset`.

- [ ] **Step 3: Verify mutual-exclusion errors print clearly**

```bash
python jules.py --repo foo --preset bugs --prompt "custom" 2>&1 | head -5
```

Expected: argparse error message about `--preset and --prompt are mutually exclusive`, exit code 2.

```bash
python jules.py --fetch abc123 --repo foo 2>&1 | head -5
```

Expected: argparse error about `--fetch cannot be combined with --repo, --prompt, or --submit`, exit code 2.

```bash
python jules.py --repo foo --submit --apply 2>&1 | head -5
```

Expected: argparse error about `--submit and --apply are mutually exclusive`, exit code 2.

- [ ] **Step 4: No commit — verification only**

---

## Task 16: Squash + push

**Files:** None (git operations)

- [ ] **Step 1: Review commits on branch**

```bash
cd ~/repos/jules-review && git log origin/main..HEAD --oneline
```

Expected: ~12-14 commits from tasks 1-14.

- [ ] **Step 2: Squash via soft reset + three logical commits**

Auto mode doesn't allow interactive rebase, so use soft reset + regroup:

```bash
cd ~/repos/jules-review
git reset --soft origin/main
git status
```

Then recreate three logical commits:

```bash
# Commit 1: docs (spec + plan)
git add docs/
git commit -m "docs: async CLI ergonomics spec and plan"

# Commit 2: tests + scaffolding
git add tests/ pytest.ini requirements.txt
git commit -m "test: add pytest scaffolding and full coverage for jules.py"

# Commit 3: feat (jules.py + README)
git add jules.py README.md
git commit -m "feat: async submit/fetch, --apply, --format diff, --preset

- submit(repo, prompt, branch) -> session_id for fire-and-forget
- fetch(session_id) -> review text
- review() composes submit + fetch; existing callers unaffected
- --apply pipes returned diff through git apply in CWD
- --format diff prints raw unified diff (pipeable to git apply)
- --preset {security,perf,bugs,docs} for canned review prompts
- Refactored CLI into main(argv) for testability
- 18+ tests covering all paths, mutual exclusions, and error cases"
```

- [ ] **Step 3: Push feature branch**

```bash
git push -u origin feat/async-cli-ergonomics
```

Expected: branch pushed.

- [ ] **Step 4: Fast-forward main and push**

```bash
git checkout main
git merge --ff-only feat/async-cli-ergonomics
git push origin main
```

Expected: main advanced to feat branch's tip.

- [ ] **Step 5: No further action — ready for Gemma integration**

The Gemma integration brainstorm is the next work item. This repo is ready.

---

## Self-Review

**Spec coverage:**
- submit() — Task 2
- fetch() — Task 3
- review() unchanged contract — Task 4
- --submit — Task 9
- --fetch — Task 10
- --apply — Task 13
- --format diff — Task 12
- --preset — Task 11 (PRESETS dict Task 7)
- Mutex rules — Tasks 10, 11, 13
- Tests — every task has red/green cycle
- README — Task 14

**Placeholder scan:** No "TBD", "TODO", "fill in details". All code blocks contain working code. All commands have expected outputs.

**Type consistency:** `submit(repo, prompt, branch) -> str` and `fetch(session_id) -> str` consistent across all references. `_extract_diff(session) -> str | None` and `_apply_diff(diff_text) -> None` used consistently. `main(argv: list[str] | None = None) -> int` stable.

**Known trade-offs:** Task 16's squash uses soft-reset instead of interactive rebase because interactive rebase needs TTY. Three-commit result (docs, tests, feat) is deliberate: matches the squash pattern used for tts-stt.
