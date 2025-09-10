import os
import requests


REPO_ID = "gpt2"
LOCAL_BASE = os.environ.get("HF_ENDPOINT", "http://127.0.0.1:8000").rstrip("/")


def _local_file_path(repo_id: str, name: str) -> str:
    return os.path.join("fake_hub", repo_id, name)


def test_single_range_prefix_bytes():
    name = "config.json"
    path = _local_file_path(REPO_ID, name)
    assert os.path.isfile(path), f"Missing local file for test: {path}"
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        data = f.read()

    r = requests.get(
        f"{LOCAL_BASE}/{REPO_ID}/resolve/main/{name}",
        headers={"Range": "bytes=0-9"},
        timeout=15,
    )
    assert r.status_code == 206, f"Expected 206, got {r.status_code}"
    assert r.headers.get("Content-Range") == f"bytes 0-9/{size}"
    assert int(r.headers.get("Content-Length", "0")) == 10
    assert r.content == data[0:10]


def test_single_range_suffix_bytes():
    name = "config.json"
    path = _local_file_path(REPO_ID, name)
    assert os.path.isfile(path), f"Missing local file for test: {path}"
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        data = f.read()

    r = requests.get(
        f"{LOCAL_BASE}/{REPO_ID}/resolve/main/{name}",
        headers={"Range": "bytes=-5"},
        timeout=15,
    )
    assert r.status_code == 206, f"Expected 206, got {r.status_code}"
    start = max(size - 5, 0)
    assert r.headers.get("Content-Range") == f"bytes {start}-{size-1}/{size}"
    assert int(r.headers.get("Content-Length", "0")) == min(5, size)
    assert r.content == data[start:]


def test_single_range_open_ended():
    name = "config.json"
    path = _local_file_path(REPO_ID, name)
    assert os.path.isfile(path), f"Missing local file for test: {path}"
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        data = f.read()

    r = requests.get(
        f"{LOCAL_BASE}/{REPO_ID}/resolve/main/{name}",
        headers={"Range": "bytes=10-"},
        timeout=15,
    )
    assert r.status_code == 206, f"Expected 206, got {r.status_code}"
    assert r.headers.get("Content-Range") == f"bytes 10-{size-1}/{size}"
    assert int(r.headers.get("Content-Length", "0")) == max(size - 10, 0)
    assert r.content == data[10:]


def test_range_unsatisfiable():
    name = "config.json"
    path = _local_file_path(REPO_ID, name)
    assert os.path.isfile(path), f"Missing local file for test: {path}"
    size = os.path.getsize(path)

    r = requests.get(
        f"{LOCAL_BASE}/{REPO_ID}/resolve/main/{name}",
        headers={"Range": f"bytes={size * 10}-"},
        timeout=15,
    )
    assert r.status_code == 416, f"Expected 416, got {r.status_code}"
    assert r.headers.get("Content-Range") == f"bytes */{size}"

