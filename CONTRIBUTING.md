# Contributing

## Commit Conventions

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>
```

**Types:** `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `ci`, `style`, `perf`

**Examples:**
- `feat(engine): add parametric gear generator`
- `fix(api): handle missing auth token gracefully`
- `docs: update installation instructions`

## Pull Request Workflow

1. Create a branch: `feat/description`, `fix/description`, or `chore/description`
2. Make changes with conventional commits
3. Open a PR against `main`
4. CI must pass (lint + tests)
5. Squash merge when ready

## Development Setup

```bash
# Clone
git clone https://github.com/Abhishekv87bit/REPO_NAME.git
cd REPO_NAME

# Install dependencies
pip install -r requirements.txt

# Run tests
pytest -v

# Lint
ruff check .
ruff format --check .
```

## Code Standards

- Python: formatted with `ruff format`, linted with `ruff check`
- TypeScript (if applicable): formatted with Prettier, linted with ESLint
- All PRs require passing CI before merge
