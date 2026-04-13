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

# Custom prompt
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

## Notes

- Reviews take **~20-25 minutes** — Jules does real work (clones, analyzes, writes patches)
- Jules requires repos to be connected via [jules.google.com](https://jules.google.com)
- Timeout: 10 minutes of polling before giving up (configurable via `POLL_TIMEOUT`)

## License

MIT
