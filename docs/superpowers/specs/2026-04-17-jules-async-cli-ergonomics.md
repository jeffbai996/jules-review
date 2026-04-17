# jules-review: async CLI + apply + diff-only + presets

**Date:** 2026-04-17
**Status:** Approved inline — proceeding to plan
**Scope:** Changes to `jules.py` only. No new files in the main source surface (tests get their own dir).

## Context

`jules.py` today is a single 189-line file that submits a review to Google's Jules API, blocks on poll for up to 60 minutes, and prints a markdown-wrapped report. It works for interactive CLI use but has friction points:

1. **Blocking polling** makes async workflows (e.g. Gemma Discord bot integration) impractical — the Python process sits holding a subprocess slot for the full 3–30 min run.
2. **Last mile is manual** — Jules returns a diff as part of its markdown report, but applying it means copy-pasting into `git apply` by hand.
3. **No machine-readable output** — for scripting, there's no way to get just the patch.
4. **Prompt repetition** — `DEFAULT_REVIEW_PROMPT` covers the general case, but targeted reviews (security-only, perf-only, bugs-only) require retyping the prompt each time.

The Gemma Discord bot integration (next project) specifically needs non-blocking submit/fetch. That makes item 1 load-bearing; the rest are quality-of-life.

## Goals

1. **Non-blocking submit/fetch** — separate submission from polling so a caller can fire-and-forget, then check back later.
2. **Zero-regression on existing callers** — `from jules import review` and the default `python jules.py --repo X` CLI invocation keep the exact same contract.
3. **Close the CLI loop** — `--apply` pipes the returned diff into `git apply` in the current repo.
4. **Diff-only output** — `--format diff` prints raw unified diff to stdout (nothing else) so it pipes cleanly: `python jules.py --repo foo --format diff | git apply`.
5. **Prompt presets** — `--preset {security,perf,bugs,docs}` maps to canned prompts; mutually exclusive with `--prompt`.
6. **Tested** — the existing codebase has no tests. Add pytest coverage for the new surface. Real API calls are out of scope (slow, paid, flaky); tests mock at the `requests` boundary.

## Non-goals

- Retrying transient HTTP failures (separate improvement; not blocking Gemma).
- Exponential backoff on poll interval (also separate).
- Session listing / history (the Jules API may or may not expose this; skip until needed).
- `--scope subdir/` for targeted file-level review (requires investigating Jules API; skip until needed).
- Config-file support (env + CLI flags cover every current use case).

## Architecture

One file, four features, additive only:

### New public API functions

```python
def submit(repo: str, prompt: str = DEFAULT_REVIEW_PROMPT, branch: str = "main") -> str:
    """Submit a Jules session. Returns session ID immediately without polling."""

def fetch(session_id: str) -> str:
    """Poll until COMPLETED, then return formatted review text. Blocks."""
```

`review()` stays identical in signature and return value. Its body becomes `fetch(submit(repo, prompt, branch))`.

### New CLI flags

- `--submit` — exit 0 after printing session ID to stdout; no polling.
- `--fetch SESSION_ID` — poll that session until done, print review. Mutually exclusive with `--repo`, `--prompt`, `--preset`, `--branch`.
- `--format {markdown,diff}` — output format. `markdown` (default) is today's output. `diff` prints only the unified diff, no summary/steps/markdown fences.
- `--apply` — after a successful review, pipe the extracted diff through `git apply` in CWD. Implies `--format diff` internally (but stdout still gets markdown unless `--format diff` is also passed — keeps human feedback alive). Exits nonzero if no diff was returned or if `git apply` fails.
- `--preset {security,perf,bugs,docs}` — override `DEFAULT_REVIEW_PROMPT`. Mutually exclusive with `--prompt`.

### Preset prompts

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

### Diff extraction helper

A new helper `_extract_diff(session: dict) -> str | None` pulls just the `gitPatch.unidiffPatch` from `sessionCompleted` artifacts. Shared between `--format diff` and `--apply`. Returns `None` if Jules made no code changes.

### `git apply` helper

```python
def _apply_diff(diff_text: str) -> None:
    """Pipe diff through `git apply` in CWD. Raises RuntimeError on failure."""
    result = subprocess.run(
        ["git", "apply"], input=diff_text, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git apply failed: {result.stderr}")
```

## CLI matrix

| Invocation | Behavior |
|---|---|
| `python jules.py --repo X` | Today's behavior (blocking review, markdown output). |
| `python jules.py --repo X --submit` | Print session ID, exit 0. |
| `python jules.py --fetch SESSION_ID` | Poll + print review. Same output format as today. |
| `python jules.py --repo X --format diff` | Blocking review, but stdout is just the diff (or empty if no diff). |
| `python jules.py --repo X --apply` | Blocking review, stdout is markdown, diff is piped to `git apply`. Exit 1 if apply fails. |
| `python jules.py --repo X --format diff --apply` | Blocking review, stdout is diff AND diff is applied. |
| `python jules.py --repo X --preset security` | Use security preset prompt. |
| `python jules.py --repo X --preset security --prompt "custom"` | ERROR: mutually exclusive. |
| `python jules.py --fetch ID --repo X` | ERROR: `--fetch` cannot combine with submission flags. |
| `python jules.py --repo X --submit --format diff` | ERROR: `--submit` just gives an ID; no review to format. |
| `python jules.py --repo X --submit --apply` | ERROR: same reason. |

## Testing

Tests live in `tests/test_jules.py`. Run with `pytest tests/` from repo root.

**Mock boundary:** `requests.post` and `requests.get` via `monkeypatch`. No real Jules API calls.

**Fixtures:** Fake session response JSON (one completed, one in-progress, one failed, one with no diff).

### Test coverage

| Test | What it verifies |
|---|---|
| `test_submit_returns_session_id` | `submit(repo, prompt)` posts to `/sessions`, returns parsed ID, does not poll |
| `test_fetch_polls_until_completed` | `fetch(id)` loops GET until `state=COMPLETED`, returns extracted review |
| `test_fetch_raises_on_failed_session` | `state=FAILED` → `RuntimeError` with session details |
| `test_fetch_raises_on_timeout` | Stuck in IN_PROGRESS → `TimeoutError` after `POLL_TIMEOUT` |
| `test_review_is_submit_plus_fetch` | `review()` output identical to `fetch(submit())` — zero regression |
| `test_extract_diff_returns_patch` | Helper pulls `gitPatch.unidiffPatch` from a completed session |
| `test_extract_diff_returns_none_for_no_changes` | Session with no artifacts → `None` |
| `test_format_diff_prints_only_patch` | CLI `--format diff`: stdout is pure diff, no "**Summary:**" headers |
| `test_format_diff_empty_when_no_changes` | `--format diff` with no-change session → empty stdout, exit 0 |
| `test_apply_invokes_git_apply` | `--apply` path: mocked `subprocess.run(["git", "apply"], ...)` receives the diff on stdin |
| `test_apply_exits_nonzero_on_git_apply_failure` | Mocked `git apply` returns nonzero → CLI exits 1 with stderr |
| `test_apply_exits_nonzero_when_no_diff` | Session has no diff → `--apply` exits 1 with clear message |
| `test_preset_security_overrides_default` | `--preset security` → `create_session` called with security prompt |
| `test_preset_and_prompt_mutually_exclusive` | Passing both → argparse error, exit 2 |
| `test_submit_and_fetch_mutually_exclusive` | `--submit --fetch ID` → argparse error, exit 2 |
| `test_submit_rejects_format_and_apply` | `--submit --format diff` → argparse error or runtime error, exit nonzero |
| `test_cli_submit_prints_session_id` | End-to-end: `--submit` prints just the session ID (no diagnostics on stdout) |
| `test_cli_fetch_prints_review` | End-to-end: `--fetch ID` prints the full markdown review |

## Plug-and-play guarantees

- Default invocation (`python jules.py --repo X`) is byte-for-byte identical to today.
- `from jules import review` unchanged.
- No new dependencies (all test scaffolding uses stdlib + pytest which we're adding to `requirements.txt`).
- Env vars unchanged (`JULES_API_KEY`, `JULES_GITHUB_USER`).

## Deployment

Single-repo, no HOST deploy needed yet (jules-review isn't a running service). If/when Gemma calls it, HOST will need:
- `git pull` in `~/repos/jules-review`
- Venv may need rebuilding if Python drift (same pattern we just hit with tts-stt)
- `.env` needs `JULES_API_KEY` + `JULES_GITHUB_USER` on HOST

Those are integration concerns, not this spec's concerns.

## Rollout

1. Implement + test on Mac.
2. Push to origin feat branch, then main.
3. Defer HOST deploy until Gemma integration actually starts.
