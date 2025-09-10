import argparse
import os
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Iterable, List, Optional, Tuple

import requests
from urllib.parse import quote


DEFAULT_ENDPOINT = os.environ.get("HF_REMOTE_ENDPOINT", "https://huggingface.co").rstrip("/")
FAKE_HUB_ROOT = os.environ.get("FAKE_HUB_ROOT", "fake_hub").rstrip("/")


@dataclass
class TreeItem:
    path: str
    type: str  # "file"/"blob" or "directory"/"tree"
    size: Optional[int] = None
    oid: Optional[str] = None  # git blob SHA-1
    lfs_oid: Optional[str] = None  # e.g., 'sha256:<hex>'
    lfs_size: Optional[int] = None


def _quote_path(repo_id: str) -> str:
    # Keep path separators in repo_id but quote each segment
    return "/".join(quote(seg, safe="") for seg in repo_id.split("/"))


def fetch_repo_tree(
    endpoint: str,
    repo_id: str,
    repo_type: str,
    revision: str,
    token: Optional[str] = None,
    timeout: int = 30,
) -> List[TreeItem]:
    """Fetch repository tree via the public API without downloading contents.

    Tries `GET /api/{repo_type}s/{repo_id}/tree/{revision}?recursive=1&expand=1`.
    Returns a flat list of file entries.
    """
    assert repo_type in {"model", "dataset"}
    rid = _quote_path(repo_id)
    url = f"{endpoint}/api/{repo_type}s/{rid}/tree/{quote(revision, safe='')}?recursive=1&expand=1"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(url, headers=headers, timeout=timeout)
    out: List[TreeItem] = []
    if r.status_code == 200:
        data = r.json()

        # Data may be a list or an object containing a list under key like 'tree'
        items = data
        if isinstance(data, dict):
            for key in ("tree", "items", "paths"):
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    break

        if isinstance(items, list):
            for it in items:
                if not isinstance(it, dict):
                    continue
                p = it.get("path") or it.get("rfilename")
                t = it.get("type") or it.get("kind")
                if not p or not t:
                    continue
                tnorm = str(t).lower()
                if tnorm in {"file", "blob"}:
                    lfs = it.get("lfs") if isinstance(it.get("lfs"), dict) else None
                    out.append(
                        TreeItem(
                            path=p,
                            type="file",
                            size=it.get("size"),
                            oid=it.get("oid") or it.get("sha"),
                            lfs_oid=(lfs.get("oid") if lfs else None),
                            lfs_size=(lfs.get("size") if lfs else None),
                        )
                    )

    # Behavior: if dataset tree is empty or unavailable, raise instead of fallback
    if repo_type == "dataset":
        if r.status_code != 200 or not out:
            raise RuntimeError(
                f"Dataset tree unavailable or empty for '{repo_id}' at {revision} ({endpoint})"
            )
        return out

    # For models: if tree missing/empty, raise (no fallback)
    if r.status_code != 200 or not out:
        raise RuntimeError(
            f"Model tree unavailable or empty for '{repo_id}' at {revision} ({endpoint})"
        )
    return out


def _apply_filters(
    paths: Iterable[TreeItem],
    includes: Optional[List[str]],
    excludes: Optional[List[str]],
    max_files: Optional[int],
) -> List[TreeItem]:
    def keep(path: str) -> bool:
        if includes:
            if not any(fnmatch(path, pat) for pat in includes):
                return False
        if excludes:
            if any(fnmatch(path, pat) for pat in excludes):
                return False
        return True

    filtered = [ti for ti in paths if keep(ti.path)]
    if max_files is not None and max_files >= 0:
        filtered = filtered[: max_files]
    return filtered


def _dest_root(repo_type: str, repo_id: str) -> str:
    # Models live under fake_hub/<repo_id>
    # Datasets live under fake_hub/datasets/<namespace>/<name>
    if repo_type == "model":
        return os.path.join(FAKE_HUB_ROOT, repo_id)
    else:
        return os.path.join(FAKE_HUB_ROOT, "datasets", repo_id)


def _safe_join(root: str, rel_path: str) -> str:
    # Prevent path traversal; normalize and ensure within root
    nroot = os.path.abspath(root)
    npath = os.path.abspath(os.path.join(root, rel_path))
    if not npath.startswith(nroot + os.sep) and npath != nroot:
        raise ValueError(f"Suspicious path outside root: {rel_path}")
    return npath


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def _touch_empty_file(p: str, force: bool = False) -> None:
    if os.path.exists(p) and not force:
        return
    _ensure_dir(os.path.dirname(p))
    # Zero profile: create empty file (0 bytes)
    with open(p, "wb") as f:
        f.write(b"")


def _parse_size(size_s: str) -> int:
    """Parse size strings like '16MB', '16MiB', '1024', '64kb'. Returns bytes.

    Supports suffixes: B, KB, MB, GB (10^3), and KiB, MiB, GiB (2^10).
    Case-insensitive; bare integers interpreted as bytes.
    """
    s = size_s.strip()
    if not s:
        raise ValueError("Empty size string")
    num = ""
    unit = ""
    for ch in s:
        if ch.isdigit():
            num += ch
        elif ch in ".,":
            # we don't support fractional; stop here
            break
        else:
            unit += ch
    if not num:
        raise ValueError(f"Invalid size: {size_s}")
    n = int(num)
    u = unit.strip().lower()
    if u in ("", "b"):
        return n
    if u in ("kb",):
        return n * 1000
    if u in ("mb",):
        return n * 1000 * 1000
    if u in ("gb",):
        return n * 1000 * 1000 * 1000
    if u in ("kib", "ki"):
        return n * 1024
    if u in ("mib", "mi"):
        return n * 1024 * 1024
    if u in ("gib", "gi"):
        return n * 1024 * 1024 * 1024
    raise ValueError(f"Unknown size unit in: {size_s}")


def _write_filled_file(p: str, size_bytes: int, pattern: bytes, force: bool = False) -> None:
    """Create file at path `p` filled by repeating `pattern` up to `size_bytes`.

    If file exists and `force` is False, the function does nothing.
    Uses chunked writes to avoid high memory usage.
    """
    if size_bytes < 0:
        raise ValueError("size_bytes must be >= 0")
    if os.path.exists(p) and not force:
        return
    _ensure_dir(os.path.dirname(p))
    if size_bytes == 0:
        with open(p, "wb") as f:
            f.write(b"")
        return

    # Ensure non-empty pattern; default to zero byte if empty
    pat = pattern or b"\x00"
    # Build a chunk of at least 1 MiB or a multiple of pattern
    target_chunk = 1024 * 1024
    reps = max(1, target_chunk // max(1, len(pat)))
    chunk = (pat * reps)[:target_chunk]
    written = 0
    with open(p, "wb") as f:
        while written + len(chunk) <= size_bytes:
            f.write(chunk)
            written += len(chunk)
        remaining = size_bytes - written
        if remaining:
            # fill the tail
            tail = (pat * ((remaining // len(pat)) + 1))[:remaining]
            f.write(tail)


def _hash_file(path: str) -> Tuple[str, str]:
    """Return (sha1_hex, sha256_hex) for file at path."""
    import hashlib

    h1 = hashlib.sha1()
    h256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h1.update(chunk)
            h256.update(chunk)
    return h1.hexdigest(), h256.hexdigest()


def _write_paths_info_sidecar(dst_root: str, created_paths: List[str], dry_run: bool = False) -> Optional[str]:
    """Write .paths-info.json reflecting ACTUAL on-disk files under dst_root.

    - Computes size and hashes from the created files to avoid metadata drift.
    - Each entry: {path, type: "file", size, oid: <sha1>, etag: <sha1>,
      lfs: {oid: "sha256:<hex>", size}}
    - If dry_run is True, returns the intended sidecar path without writing.
    """
    entries = []
    for abs_path in created_paths:
        if not os.path.isfile(abs_path):
            continue
        rel = os.path.relpath(abs_path, dst_root).replace(os.sep, "/")
        size = os.path.getsize(abs_path)
        sha1_hex, sha256_hex = _hash_file(abs_path)
        rec = {
            "path": rel,
            "type": "file",
            "size": int(size),
            "oid": sha1_hex,
            "etag": sha1_hex,
            "lfs": {"oid": f"sha256:{sha256_hex}", "size": int(size)},
        }
        entries.append(rec)

    if not entries:
        return None
    sidecar_path = os.path.join(dst_root, ".paths-info.json")
    if dry_run:
        return sidecar_path
    _ensure_dir(dst_root)
    import json

    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "entries": entries}, f, ensure_ascii=False, indent=2)
    return sidecar_path


def generate_skeleton(
    repo_type: str,
    repo_id: str,
    files: List[TreeItem],
    dst_root: Optional[str] = None,
    force: bool = False,
    dry_run: bool = False,
    fill_size: Optional[int] = None,
    fill_pattern: Optional[bytes] = None,
) -> Tuple[str, List[str]]:
    root = dst_root or _dest_root(repo_type, repo_id)
    # Ensure root exists even if there are no files (defensive)
    _ensure_dir(root)
    created: List[str] = []
    for it in files:
        abs_p = _safe_join(root, it.path)
        if dry_run:
            created.append(abs_p)
            continue
        if fill_size is not None:
            _write_filled_file(abs_p, size_bytes=fill_size, pattern=fill_pattern or b"", force=force)
        else:
            _touch_empty_file(abs_p, force=force)
        created.append(abs_p)
    return root, created


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="fakehub-skeleton",
        description="Skeletonize a real HF repo (structure + filenames only)",
    )
    p.add_argument("repo_id", help="Repository ID, e.g. 'gpt2' or 'org/name'")
    p.add_argument(
        "-t",
        "--repo-type",
        choices=["model", "dataset"],
        required=True,
        help="Repository type",
    )
    p.add_argument(
        "-r",
        "--revision",
        default="main",
        help="Revision/branch/commit (default: main)",
    )
    p.add_argument(
        "-e",
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"Remote endpoint (default: {DEFAULT_ENDPOINT})",
    )
    p.add_argument("--token", default=os.environ.get("HF_TOKEN"), help="HF access token (optional)")
    p.add_argument("--include", action="append", help="Glob to include (can repeat)")
    p.add_argument("--exclude", action="append", help="Glob to exclude (can repeat)")
    p.add_argument("--max-files", type=int, default=None, help="Limit number of files")
    p.add_argument("--dst", default=None, help="Destination root (override default layout)")
    p.add_argument("--force", action="store_true", help="Overwrite existing files")
    p.add_argument("--dry-run", action="store_true", help="Print actions without writing files")
    # Filler options
    p.add_argument(
        "--fill",
        action="store_true",
        help="Fill created files with repeated content instead of empty files",
    )
    p.add_argument(
        "--fill-size",
        default=None,
        help="Per-file size to fill, e.g. '16MiB' (default if --fill is set)",
    )
    p.add_argument(
        "--fill-content",
        default=None,
        help="Content string to repeat when filling files (default: zeros)",
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    # Only remote fetch is supported now (no local dir mode)
    try:
        items = fetch_repo_tree(
            endpoint=args.endpoint,
            repo_id=args.repo_id,
            repo_type=args.repo_type,
            revision=args.revision,
            token=args.token,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    files = _apply_filters(items, args.include, args.exclude, args.max_files)
    dst_root = args.dst or _dest_root(args.repo_type, args.repo_id)
    # Resolve filler configuration
    fill_size_bytes: Optional[int] = None
    fill_pattern_bytes: Optional[bytes] = None
    if getattr(args, "fill", False):
        if args.fill_size is None:
            # Default to 16 MiB when --fill is enabled and no size specified
            fill_size_bytes = 16 * 1024 * 1024
        else:
            try:
                fill_size_bytes = _parse_size(str(args.fill_size))
            except Exception as e:
                print(f"Error parsing --fill-size: {e}", file=sys.stderr)
                return 2
        fill_pattern_bytes = (args.fill_content.encode("utf-8") if args.fill_content else b"")
    root, created = generate_skeleton(
        repo_type=args.repo_type,
        repo_id=args.repo_id,
        files=files,
        dst_root=dst_root,
        force=args.force,
        dry_run=args.dry_run,
        fill_size=fill_size_bytes,
        fill_pattern=fill_pattern_bytes,
    )

    # Persist paths-info based on actual files to avoid confusion
    try:
        sidecar = _write_paths_info_sidecar(root, created, dry_run=args.dry_run)
        if sidecar:
            print(f"Wrote sidecar: {sidecar}")
    except Exception as e:
        print(f"Warning: failed to write .paths-info.json: {e}", file=sys.stderr)

    print(f"Skeleton root: {root}")
    print(f"Files: {len(created)}")
    for p in created:
        rel = os.path.relpath(p, root)
        print(f"  {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
