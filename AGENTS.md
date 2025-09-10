# Repository Guidelines

## Project Structure & Module Organization
- `main.py`: FastAPI app exposing Hub-compatible routes.
- `fake_hub/`: root store; models in `fake_hub/<repo_id>/` (e.g., `fake_hub/gpt2/`); datasets in `fake_hub/datasets/<namespace>/<name>/` (e.g., `fake_hub/datasets/HuggingFaceFW/finepdfs/`).
- `skeleton.py`: CLI to generate local skeletons (structure-only) for models/datasets; remote-only (no local-dir mode).
- `tests/test_api_compat.py`: integration tests comparing local API vs `hf-mirror.com` for `gpt2`.
- `pyproject.toml`: project metadata and runtime deps (`fastapi`, `uvicorn`).
- `README.md`: usage, compatibility, troubleshooting.

## Build, Test, and Development Commands
- Install deps: `uv sync` (Python 3.12+).
- Run dev server (auto-reload): `uv run python -m uvicorn main:app --reload`.
- Bind to LAN: `uv run python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload`.
- Point CLI: `export HF_ENDPOINT=http://127.0.0.1:8000`.
- Run tests: `uv run pytest -vs tests/test_api_compat.py` (models) and `uv run pytest -vs tests/test_dataset_api_compat.py` (datasets).
- Optional without uv: `python -m uvicorn main:app --reload` (ensure deps installed).
- Server prints `[fake-hub] FAKE_HUB_ROOT = ...` on startup.

## Coding Style & Naming Conventions
- Follow PEP 8; 4-space indentation; max line length ~100.
- Names: `snake_case` functions/vars, `PascalCase` classes, `UPPER_SNAKE` constants.
- Prefer type hints and docstrings for public functions.
- Keep API schema stable with hf-mirror parity:
  - Models: `/api/models/{repo_id}` and `/api/models/{repo_id}/revision/{revision}` must return the same top-level key set and types as `hf-mirror.com` for `gpt2`.
  - Datasets: `/api/datasets/{id}` and `/api/datasets/{id}/revision/{revision}` should include common fields (`id`, `sha`, `lastModified`, `cardData`, `siblings`, …). Local keys are a subset of remote keys in tests to avoid brittleness across datasets.
  - `siblings` items should only contain `rfilename` (both models and datasets).
- Implement and keep parity for `paths-info` endpoints used by newer clients:
  - `POST /api/models/{repo_id}/paths-info/{revision}` and `POST /api/datasets/{repo_id}/paths-info/{revision}`.
  - Accept body `{paths?: string[], expand?: boolean}`; return list of `{path, type: "file"|"directory", size?[, oid][, lfs: {oid, size}]}`.
  - Guarantee: every file entry must include either `oid` (SHA‑1) or `lfs.oid` (`sha256:<hex>`); prefer using sidecar when sizes match; otherwise compute on demand.
- Routes must use `{repo_id:path}` for org/name repos (e.g., `openai/gpt-oss-20b`). Register specific routes (e.g., `/revision/...`, `/paths-info/...`) before generic `/api/models/{repo_id}` to avoid shadowing.
- Imports grouped: stdlib, third-party, local; one blank line between groups.

## Testing Guidelines
- Framework: `pytest`. Place tests in `tests/` as `test_*.py`.
- Primary suites: `tests/test_api_compat.py` for models and `tests/test_dataset_api_compat.py` for datasets (both require network to reach `hf-mirror.com`; auto-skip if unreachable).
- Contract: tests assert full metadata field-set equality and type-match vs mirror; do not remove/rename fields without updating tests and README.
- Aim for ≥80% coverage on changed code; add fixtures for temp `fake_hub/` layouts.
 - For file layout-dependent tests, prefer using `FAKE_HUB_ROOT` and temp dirs.

## Commit & Pull Request Guidelines
- Use Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- PRs must include: purpose, linked issues, steps to verify (commands/cURL), and docs updates when routes或行为变更（更新 README 与 tests）。
- Keep diffs focused and small; avoid unrelated refactors.

## Security & Configuration Tips
- Server reads files under `fake_hub/` only; avoid path traversal. Do not commit large/secret model files; store locally and gitignore as needed.
- `.bin` files can be large; avoid adding to git; prefer local-only.
- Use `HF_HOME=$PWD/.hf_home` for isolated client cache when testing.
- Configure `FAKE_HUB_ROOT` via env; prefer absolute paths when running under tools that change CWD. Startup logs print the resolved root.
- Be mindful of route precedence to prevent 404 from path shadowing.

## Agent-Specific Instructions
- Keep changes minimal and localized; do not rename files or alter public routes without consensus.
- If touching API responses, run `uv run pytest -vs tests/test_api_compat.py` and update README accordingly.
- Avoid introducing new tooling unless necessary.
- When adding endpoints or changing routing, ensure `{repo_id:path}` support and update both README and tests.
- For skeleton changes, keep remote-only generation; sidecar must reflect on-disk files (actual size + hashes), not remote metadata.
