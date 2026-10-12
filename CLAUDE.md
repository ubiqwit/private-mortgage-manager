# Notes for Claude

## Commits
- The repo owner (ubiqwit) is the **author** of every commit; Claude is the **committer**, so commits stay signed
  and show as Verified on GitHub. Once per session:
  ```
  git config author.name "ubiqwit"
  git config author.email "124944176+ubiqwit@users.noreply.github.com"
  git config committer.name "Claude"
  git config committer.email "noreply@anthropic.com"
  ```
- Push straight to `main`; no pull requests.
- Before committing: `.venv/bin/ruff check app tests migrations` and `.venv/bin/pytest -q`.
