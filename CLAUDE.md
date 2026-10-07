# Notes for Claude

## Commits
- Author every commit as the repo owner:
  `git -c user.name="ubiqwit" -c user.email="124944176+ubiqwit@users.noreply.github.com" commit …`
  (or set those two values with `git config` once per session).
- Push straight to `main`; no pull requests.
- Before committing: `.venv/bin/ruff check app tests migrations` and `.venv/bin/pytest -q`.
