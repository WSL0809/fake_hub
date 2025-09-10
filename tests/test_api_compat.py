import os
from typing import Any, Dict, List, Tuple

import pytest
import requests


# Configuration
REPO_ID = "gpt2"
LOCAL_BASE = os.environ.get("HF_ENDPOINT", "http://127.0.0.1:8000").rstrip("/")
REMOTE_BASE = os.environ.get("MIRROR_ENDPOINT", "https://hf-mirror.com").rstrip("/")


def _get_json(base: str, path: str) -> Dict[str, Any]:
    url = f"{base}{path}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def _head(base: str, path: str) -> requests.Response:
    url = f"{base}{path}"
    # Follow redirects on remote mirror/CDN
    r = requests.head(url, allow_redirects=True, timeout=15)
    return r


def _normalize_types(obj: Any) -> Any:
    """Map JSON-like object to a type skeleton for comparison."""
    if isinstance(obj, dict):
        return {k: _normalize_types(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [(_normalize_types(obj[0]) if obj else None)]
    return type(obj).__name__


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


def _check_remote_available() -> bool:
    try:
        r = requests.get(f"{REMOTE_BASE}/api/models/{REPO_ID}", timeout=10)
        return r.status_code == 200
    except Exception:
        return False


REMOTE_AVAILABLE = _check_remote_available()


@pytest.mark.skipif(not REMOTE_AVAILABLE, reason="Remote mirror not reachable")
def test_model_info_schema_equal():
    local = _get_json(LOCAL_BASE, f"/api/models/{REPO_ID}")
    remote = _get_json(REMOTE_BASE, f"/api/models/{REPO_ID}")

    # Assert key sets equal
    lk, rk = set(local.keys()), set(remote.keys())
    assert lk == rk, f"Top-level keys differ. Only in local: {lk - rk}; Only in remote: {rk - lk}"

    # Assert types equal for all keys
    for k in sorted(lk):
        lt, rt = type(local[k]).__name__, type(remote[k]).__name__
        assert lt == rt, f"Type mismatch at key '{k}': local={lt}, remote={rt}"


@pytest.mark.skipif(not REMOTE_AVAILABLE, reason="Remote mirror not reachable")
def test_model_info_revision_schema_equal():
    local = _get_json(LOCAL_BASE, f"/api/models/{REPO_ID}/revision/main")
    remote = _get_json(REMOTE_BASE, f"/api/models/{REPO_ID}/revision/main")

    lk, rk = set(local.keys()), set(remote.keys())
    assert lk == rk, f"Top-level keys differ (revision). Only in local: {lk - rk}; Only in remote: {rk - lk}"

    for k in sorted(lk):
        lt, rt = type(local[k]).__name__, type(remote[k]).__name__
        assert lt == rt, f"Type mismatch at key '{k}' (revision): local={lt}, remote={rt}"


@pytest.mark.skipif(not REMOTE_AVAILABLE, reason="Remote mirror not reachable")
def test_siblings_item_schema_equal():
    local = _get_json(LOCAL_BASE, f"/api/models/{REPO_ID}")
    remote = _get_json(REMOTE_BASE, f"/api/models/{REPO_ID}")

    lmap, rmap = _siblings_by_name(local), _siblings_by_name(remote)
    common = _common_files(local, remote)
    assert common, "No common files found between local and remote siblings"

    for name in common:
        li, ri = lmap[name], rmap[name]
        lk, rk = set(li.keys()), set(ri.keys())
        assert lk == rk, f"Sibling '{name}' keys differ. local-only: {lk - rk}; remote-only: {rk - lk}"

        # lfs may be null or object; types must match
        if "lfs" in lk:
            lt, rt = type(li["lfs"]).__name__, type(ri["lfs"]).__name__
            assert lt == rt, f"Sibling '{name}' lfs type mismatch: local={lt}, remote={rt}"
            if isinstance(li["lfs"], dict) and isinstance(ri["lfs"], dict):
                llk, rrk = set(li["lfs"].keys()), set(ri["lfs"].keys())
                assert llk == rrk, (
                    f"Sibling '{name}' lfs keys differ. local-only: {llk - rrk}; remote-only: {rrk - llk}"
                )


def _pick_files_for_fetch(local: Dict[str, Any], remote: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    common = _common_files(local, remote)
    # Prefer small text files for GET; keep binary for HEAD only
    text_candidates = [n for n in common if n.endswith(".json") or n.endswith(".txt") or n == ".gitattributes"]
    bin_candidates = [n for n in common if n not in text_candidates]
    get_files = text_candidates[:2] or common[:1]
    head_only = []
    if bin_candidates:
        head_only.append(bin_candidates[0])
    return get_files, head_only


@pytest.mark.skipif(not REMOTE_AVAILABLE, reason="Remote mirror not reachable")
def test_file_head_and_get():
    local_meta = _get_json(LOCAL_BASE, f"/api/models/{REPO_ID}/revision/main")
    remote_meta = _get_json(REMOTE_BASE, f"/api/models/{REPO_ID}/revision/main")

    local_sha = local_meta.get("sha") or "main"
    remote_sha = remote_meta.get("sha") or "main"

    get_files, head_only = _pick_files_for_fetch(local_meta, remote_meta)

    # HEAD checks for text and binary
    for name in get_files + head_only:
        lr = _head(LOCAL_BASE, f"/{REPO_ID}/resolve/{local_sha}/{name}")
        rr = _head(REMOTE_BASE, f"/{REPO_ID}/resolve/{remote_sha}/{name}")
        assert lr.status_code == 200, f"Local HEAD failed for {name}: {lr.status_code}"
        assert 200 <= rr.status_code < 400, f"Remote HEAD failed for {name}: {rr.status_code}"
        # Basic header presence on local
        for h in ["Content-Length", "Content-Type", "Accept-Ranges", "ETag"]:
            assert h in lr.headers, f"Local HEAD missing header {h} for {name}"

    # GET checks for small text files only
    for name in get_files:
        lr = requests.get(f"{LOCAL_BASE}/{REPO_ID}/resolve/{local_sha}/{name}", timeout=30)
        rr = requests.get(f"{REMOTE_BASE}/{REPO_ID}/resolve/{remote_sha}/{name}", timeout=30)
        assert lr.status_code == 200, f"Local GET failed for {name}: {lr.status_code}"
        assert 200 <= rr.status_code < 400, f"Remote GET failed for {name}: {rr.status_code}"


@pytest.mark.skipif(not REMOTE_AVAILABLE, reason="Remote mirror not reachable")
def test_not_found_cases():
    missing_repo = "this-repo-does-not-exist-xyz"
    lr = requests.get(f"{LOCAL_BASE}/api/models/{missing_repo}", timeout=10)
    rr = requests.get(f"{REMOTE_BASE}/api/models/{missing_repo}", timeout=10)
    assert lr.status_code == 404, f"Local missing repo expected 404, got {lr.status_code}"
    assert 400 <= rr.status_code < 500, f"Remote missing repo expected 4xx, got {rr.status_code}"

    # Missing file under existing repo
    local_sha = _get_json(LOCAL_BASE, f"/api/models/{REPO_ID}/revision/main").get("sha") or "main"
    remote_sha = _get_json(REMOTE_BASE, f"/api/models/{REPO_ID}/revision/main").get("sha") or "main"
    missing_file = "__this_file_should_not_exist__.bin"
    lrh = _head(LOCAL_BASE, f"/{REPO_ID}/resolve/{local_sha}/{missing_file}")
    rrh = _head(REMOTE_BASE, f"/{REPO_ID}/resolve/{remote_sha}/{missing_file}")
    assert lrh.status_code == 404, f"Local missing file HEAD expected 404, got {lrh.status_code}"
    assert 400 <= rrh.status_code < 500, f"Remote missing file HEAD expected 4xx, got {rrh.status_code}"
