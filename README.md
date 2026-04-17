# jules-review

Async client for the [Google Jules](https://jules.google.com) code review API. Submit a session, poll until done, get back a git diff.

Jules is a code *fixer*, not a prose reviewer — output is a unified diff + suggested commit message, not analysis paragraphs.

## Setup

```bash
git clone https://github.com/jeffbai996/jules-review.git
cd jules-review
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in your values
```

**.env**
```
JULES_API_KEY=your_google_api_key
JULES_GITHUB_USER=your_github_username
```

Your GitHub account must be connected to Jules at [jules.google.com](https://jules.google.com) before use.

## Usage

**CLI**
```bash
# Review a repo (uses default prompt)
python jules.py --repo my-repo

# Canned prompt presets
python jules.py --repo my-repo --preset security
python jules.py --repo my-repo --preset perf
python jules.py --repo my-repo --preset bugs
python jules.py --repo my-repo --preset docs

# Custom prompt (overrides --preset)
python jules.py --repo my-repo --prompt "Check for security vulnerabilities"

# Specific branch
python jules.py --repo my-repo --branch feature/my-branch

# Autodetect repo from current directory's git remote
cd ~/repos/my-repo && python jules.py
```

**Python**
```python
from jules import review

result = review("my-repo", "Review for bugs and logic errors", branch="main")
print(result)
```

## Output

```
**Summary:**
fix: handle edge case in payment processor when amount is zero

**Steps taken:**
- Read payment processing module
- Identified missing zero-amount guard in charge()
- Added early return with validation error

**Diff:**
--- a/payments.py
+++ b/payments.py
@@ -12,6 +12,8 @@ def charge(amount, card):
+    if amount <= 0:
+        raise ValueError(f"Invalid amount: {amount}")
```

The diff is applicable directly: `git apply <diff-file>`

## Async workflow

For bots, scripts, or anything that can't block for 10–30 min:

```bash
# Submit and get session ID immediately (no polling)
SESSION=$(python jules.py --repo my-repo --submit)

# Later (from anywhere), fetch the result
python jules.py --fetch "$SESSION"
```

## Apply patches directly

```bash
# Review + apply diff to current working directory in one shot
python jules.py --repo my-repo --apply

# Machine-readable output (pipe to git or a file)
python jules.py --repo my-repo --format diff > review.patch
python jules.py --repo my-repo --format diff | git apply
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

## Notes

- Reviews take **~20-25 minutes** — Jules does real work (clones, analyzes, writes patches)
- Jules requires repos to be connected via [jules.google.com](https://jules.google.com)
- Timeout: 60 minutes of polling before giving up (configurable via `POLL_TIMEOUT`)

## License

MIT
