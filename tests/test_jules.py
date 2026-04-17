"""Tests for jules.py."""
from unittest.mock import patch
import pytest
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

    assert "**Summary:**" in result
    assert "fix: bump x" in result
    assert "**Diff:**" in result


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


def test_extract_diff_returns_patch(completed_session_with_diff):
    """_extract_diff() pulls gitPatch.unidiffPatch from a completed session."""
    diff = jules._extract_diff(completed_session_with_diff)
    assert diff is not None
    assert "diff --git a/foo.py b/foo.py" in diff
    assert "x = 2" in diff


def test_extract_diff_returns_none_for_no_changes(completed_session_no_diff):
    """_extract_diff() returns None when the session made no code changes."""
    assert jules._extract_diff(completed_session_no_diff) is None


def test_presets_available():
    """PRESETS dict contains all four documented presets."""
    assert set(jules.PRESETS.keys()) == {"security", "perf", "bugs", "docs"}
    assert "security" in jules.PRESETS["security"].lower()
    assert "performance" in jules.PRESETS["perf"].lower()
    assert "bugs" in jules.PRESETS["bugs"].lower()
    assert "documentation" in jules.PRESETS["docs"].lower()


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

    called_json = mock_post.call_args.kwargs["json"]
    assert "security" in called_json["prompt"].lower()
    assert "injection" in called_json["prompt"].lower()


def test_main_preset_and_prompt_mutually_exclusive():
    """--preset X --prompt 'custom' is an error."""
    with pytest.raises(SystemExit) as exc:
        jules.main(["--repo", "my-repo", "--preset", "bugs", "--prompt", "custom"])
    assert exc.value.code != 0


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
    assert captured.out.strip() == ""


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
    assert "**Summary:**" in captured.out

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
