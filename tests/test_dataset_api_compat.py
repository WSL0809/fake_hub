import os
from typing import Any, Dict, List, Tuple

import pytest
import requests


# Configuration
DATASET_ID = "HuggingFaceFW/finepdfs"
LOCAL_BASE = os.environ.get("HF_ENDPOINT", "http://127.0.0.1:8000").rstrip("/")
REMOTE_BASE = os.environ.get("MIRROR_ENDPOINT", "https://hf-mirror.com").rstrip("/")


def _get_json(base: str, path: str) -> Dict[str, Any]:
    url = f"{base}{path}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def _head(base: str, path: str) -> requests.Response:
    url = f"{base}{path}"
    r = requests.head(url, allow_redirects=True, timeout=15)
    return r


def _siblings_by_name(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    sibs = data.get("siblings") or []
    out: Dict[str, Dict[str, Any]] = {}
    for it in sibs:
        name = it.get("rfilename")
        if isinstance(name, str):
            out[name] = it
    return out


def _common_files(a: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    sa = set(_siblings_by_name(a).keys())
    sb = set(_siblings_by_name(b).keys())
    return sorted(sa & sb)


def _pick_files_for_fetch(local: Dict[str, Any], remote: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    common = _common_files(local, remote)
    # Prefer simple text files and metadata for GET
    text_candidates = [n for n in common if n.endswith(".json") or n.endswith(".jsonl") or n.endswith(".md") or n == ".gitattributes"]
    bin_candidates = [n for n in common if n not in text_candidates]
    get_files = text_candidates[:2] or common[:1]
    head_only = []
    if bin_candidates:
        head_only.append(bin_candidates[0])
    return get_files, head_only


def _check_remote_available() -> bool:
    try:
        r = requests.get(f"{REMOTE_BASE}/api/datasets/{DATASET_ID}", timeout=10)
        return r.status_code == 200
    except Exception:
        return False


REMOTE_AVAILABLE = _check_remote_available()


@pytest.mark.skipif(not REMOTE_AVAILABLE, reason="Remote mirror not reachable")
def test_dataset_info_schema_subset():
    local = _get_json(LOCAL_BASE, f"/api/datasets/{DATASET_ID}")
    remote = _get_json(REMOTE_BASE, f"/api/datasets/{DATASET_ID}")

    lk, rk = set(local.keys()), set(remote.keys())
    # Local keys should be a subset of remote keys
    assert lk.issubset(rk), f"Local keys not subset of remote. Extra: {lk - rk}"

    # Types must match for all local keys
    for k in sorted(lk):
        lt, rt = type(local[k]).__name__, type(remote[k]).__name__
        assert lt == rt, f"Type mismatch at key '{k}': local={lt}, remote={rt}"


@pytest.mark.skipif(not REMOTE_AVAILABLE, reason="Remote mirror not reachable")
def test_dataset_info_revision_schema_subset():
    local = _get_json(LOCAL_BASE, f"/api/datasets/{DATASET_ID}/revision/main")
    remote = _get_json(REMOTE_BASE, f"/api/datasets/{DATASET_ID}/revision/main")

    lk, rk = set(local.keys()), set(remote.keys())
    assert lk.issubset(rk), f"Local keys not subset (revision). Extra: {lk - rk}"

    for k in sorted(lk):
        lt, rt = type(local[k]).__name__, type(remote[k]).__name__
        assert lt == rt, f"Type mismatch at key '{k}' (revision): local={lt}, remote={rt}"


@pytest.mark.skipif(not REMOTE_AVAILABLE, reason="Remote mirror not reachable")
def test_dataset_file_head_and_get():
    local_meta = _get_json(LOCAL_BASE, f"/api/datasets/{DATASET_ID}/revision/main")
    remote_meta = _get_json(REMOTE_BASE, f"/api/datasets/{DATASET_ID}/revision/main")

    local_sha = local_meta.get("sha") or "main"
    remote_sha = remote_meta.get("sha") or "main"

    get_files, head_only = _pick_files_for_fetch(local_meta, remote_meta)
    assert get_files or head_only, "No overlapping files to test between local and remote"

    # HEAD checks
    for name in get_files + head_only:
        lr = _head(LOCAL_BASE, f"/datasets/{DATASET_ID}/resolve/{local_sha}/{name}")
        rr = _head(REMOTE_BASE, f"/datasets/{DATASET_ID}/resolve/{remote_sha}/{name}")
        assert lr.status_code == 200, f"Local HEAD failed for {name}: {lr.status_code}"
        assert 200 <= rr.status_code < 400, f"Remote HEAD failed for {name}: {rr.status_code}"
        for h in ["Content-Length", "Content-Type", "Accept-Ranges", "ETag"]:
            assert h in lr.headers, f"Local HEAD missing header {h} for {name}"

    # GET small text files
    for name in get_files:
        lr = requests.get(f"{LOCAL_BASE}/datasets/{DATASET_ID}/resolve/{local_sha}/{name}", timeout=30)
        rr = requests.get(f"{REMOTE_BASE}/datasets/{DATASET_ID}/resolve/{remote_sha}/{name}", timeout=30)
        assert lr.status_code == 200, f"Local GET failed for {name}: {lr.status_code}"
        assert 200 <= rr.status_code < 400, f"Remote GET failed for {name}: {rr.status_code}"


@pytest.mark.skipif(not REMOTE_AVAILABLE, reason="Remote mirror not reachable")
def test_dataset_not_found_cases():
    missing_repo = "this-dataset-does-not-exist-xyz/abc"
    lr = requests.get(f"{LOCAL_BASE}/api/datasets/{missing_repo}", timeout=10)
    rr = requests.get(f"{REMOTE_BASE}/api/datasets/{missing_repo}", timeout=10)
    assert lr.status_code == 404, f"Local missing dataset expected 404, got {lr.status_code}"
    assert 400 <= rr.status_code < 500, f"Remote missing dataset expected 4xx, got {rr.status_code}"

    local_sha = _get_json(LOCAL_BASE, f"/api/datasets/{DATASET_ID}/revision/main").get("sha") or "main"
    remote_sha = _get_json(REMOTE_BASE, f"/api/datasets/{DATASET_ID}/revision/main").get("sha") or "main"
    missing_file = "__this_file_should_not_exist__.bin"
    lrh = _head(LOCAL_BASE, f"/datasets/{DATASET_ID}/resolve/{local_sha}/{missing_file}")
    rrh = _head(REMOTE_BASE, f"/datasets/{DATASET_ID}/resolve/{remote_sha}/{missing_file}")
    assert lrh.status_code == 404, f"Local missing file HEAD expected 404, got {lrh.status_code}"
    assert 400 <= rrh.status_code < 500, f"Remote missing file HEAD expected 4xx, got {rrh.status_code}"

