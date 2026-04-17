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
