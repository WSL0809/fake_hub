import os
import sys
from pathlib import Path

import pytest
import requests

# Ensure project root is importable when running under uv/pytest
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skeleton import (  # type: ignore
    fetch_repo_tree,
    generate_skeleton,
    _apply_filters,
)


REMOTE_BASE = os.environ.get("MIRROR_ENDPOINT", "https://hf-mirror.com").rstrip("/")


def _remote_ok(path: str) -> bool:
    try:
        r = requests.get(f"{REMOTE_BASE}{path}", timeout=10)
        return r.status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _remote_ok("/api/models/gpt2"), reason="Remote mirror not reachable")
def test_skeletonize_model_minimal(tmp_path: Path):
    try:
        files = fetch_repo_tree(
            endpoint=REMOTE_BASE,
            repo_id="gpt2",
            repo_type="model",
            revision="main",
        )
    except RuntimeError:
        pytest.skip("Model tree unavailable or empty on mirror; CLI intentionally errors")
    # Keep just one predictable small file
    filtered = _apply_filters(files, includes=["config.json"], excludes=None, max_files=1)
    root = tmp_path / "fake_hub" / "gpt2"
    out_root, created = generate_skeleton(
        repo_type="model",
        repo_id="gpt2",
        files=filtered,
        dst_root=str(root),
        force=True,
    )
    assert Path(out_root).exists()
    assert created and Path(created[0]).exists()
    assert Path(created[0]).stat().st_size == 0


@pytest.mark.skipif(
    not _remote_ok("/api/datasets/HuggingFaceFW/finepdfs"),
    reason="Remote mirror not reachable",
)
def test_skeletonize_dataset_any_one_file(tmp_path: Path):
    try:
        files = fetch_repo_tree(
            endpoint=REMOTE_BASE,
            repo_id="HuggingFaceFW/finepdfs",
            repo_type="dataset",
            revision="main",
        )
    except RuntimeError:
        pytest.skip("Dataset tree unavailable or empty on mirror; CLI intentionally errors")
    # Take the first file only to keep it light
    filtered = _apply_filters(files, includes=None, excludes=None, max_files=1)
    root = tmp_path / "fake_hub" / "datasets" / "HuggingFaceFW" / "finepdfs"
    out_root, created = generate_skeleton(
        repo_type="dataset",
        repo_id="HuggingFaceFW/finepdfs",
        files=filtered,
        dst_root=str(root),
        force=True,
    )
    assert Path(out_root).exists()
    assert created and Path(created[0]).exists()
    assert Path(created[0]).stat().st_size == 0
