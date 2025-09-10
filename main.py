import os
import time
import uuid
import json as _json
import hashlib
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.requests import Request
import uvicorn

app = FastAPI()

# Logging configuration
LOG_REQUESTS = os.environ.get("LOG_REQUESTS", "1") not in {"0", "false", "False"}
LOG_BODY_MAX = int(os.environ.get("LOG_BODY_MAX", "4096"))  # max bytes of body to log
LOG_HEADERS_MODE = os.environ.get("LOG_HEADERS", "all").lower()  # 'all'|'minimal'
LOG_RESP_HEADERS = os.environ.get("LOG_RESP_HEADERS", "1") not in {"0", "false", "False"}
LOG_REDACT = os.environ.get("LOG_REDACT", "1") not in {"0", "false", "False"}
LOG_BODY_ALL = os.environ.get("LOG_BODY_ALL", "1") not in {"0", "false", "False"}
_logger = logging.getLogger("fakehub")
if not _logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@app.middleware("http")
async def _log_http_requests(request, call_next):
    """Log inbound requests with method, path, query, selected headers and optional JSON body.

    - Limits logged body size by LOG_BODY_MAX.
    - Avoids excessive logging for large/binary downloads by not attempting to read body unless JSON.
    """
    if not LOG_REQUESTS:
        return await call_next(request)

    req_id = uuid.uuid4().hex[:12]
    method = request.method
    path = request.url.path
    query = request.url.query
    client_host = getattr(request.client, "host", None)
    client_port = getattr(request.client, "port", None)
    headers = request.headers
    content_type = headers.get("content-type", "")
    scheme = request.url.scheme
    http_version = request.scope.get("http_version") or request.scope.get("server") or "-"

    # Build headers snapshot with optional redaction
    def _redact(k: str, v: str) -> str:
        if LOG_REDACT and k.lower() in {"authorization", "cookie", "set-cookie", "proxy-authorization", "x-api-key", "x-hf-token"}:
            return "***"
        return v
    headers_snapshot = {k: _redact(k, v) for k, v in headers.items()} if LOG_HEADERS_MODE == "all" else {
        "user-agent": headers.get("user-agent", "-"),
        "content-type": headers.get("content-type", "-"),
        "range": headers.get("range") or headers.get("Range") or "-",
        "content-length": headers.get("content-length", "-"),
        "accept": headers.get("accept", "-"),
        "referer": headers.get("referer", "-"),
        "origin": headers.get("origin", "-"),
    }

    # Decide whether to log body: only for JSON-like requests
    body_snippet = None
    if LOG_BODY_ALL or ("application/json" in content_type.lower()):
        try:
            raw = await request.body()
            if raw:
                body_snippet = raw[:LOG_BODY_MAX].decode("utf-8", errors="replace")
        except Exception:
            body_snippet = None

    _logger.info(
        "[%s] HTTP %s %s%s from %s%s proto=%s scheme=%s",
        req_id,
        method,
        path,
        ("?" + query) if query else "",
        client_host or "-",
        (f":{client_port}" if client_port is not None else ""),
        http_version,
        scheme,
    )
    _logger.info("[%s] Headers: %s", req_id, _json.dumps(headers_snapshot, ensure_ascii=False))
    if body_snippet is not None:
        _logger.info("[%s] Body[<=%d]: %s", req_id, LOG_BODY_MAX, body_snippet)

    started = time.time()
    status = "-"
    try:
        response = await call_next(request)
        status = getattr(response, "status_code", "-")
        # Attach request id for correlation
        try:
            response.headers["X-Request-ID"] = req_id
        except Exception:
            pass
        return response
    except Exception:
        _logger.exception("[%s] Unhandled error while processing %s %s", req_id, method, path)
        raise
    finally:
        dur_ms = int((time.time() - started) * 1000)
        # Try to log response headers minimally (content-type/length) if available
        try:
            resp_ct = locals().get("response").headers.get("content-type", "-") if "response" in locals() else "-"
            resp_len = locals().get("response").headers.get("content-length", "-") if "response" in locals() else "-"
            _logger.info("[%s] Response %s %s -> %s (%d ms) ct=%s len=%s", req_id, method, path, status, dur_ms, resp_ct, resp_len)
            if LOG_RESP_HEADERS and "response" in locals() and getattr(locals().get("response"), "headers", None) is not None:
                # Redact Set-Cookie if needed
                resp_headers = {k: _redact(k, v) for k, v in locals()["response"].headers.items()}
                _logger.info("[%s] Response headers: %s", req_id, _json.dumps(resp_headers, ensure_ascii=False))
        except Exception:
            _logger.info("[%s] Response %s %s -> %s (%d ms)", req_id, method, path, status, dur_ms)

# 模拟存放模型/数据集的根目录，可通过环境变量覆盖
FAKE_HUB_ROOT = os.environ.get("FAKE_HUB_ROOT", "fake_hub")

def get_file_size(filepath):
    """获取文件大小（字节）"""
    return os.path.getsize(filepath)

def get_file_sha256(filepath):
    """计算文件的 SHA256 哈希值"""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def get_file_hashes(filepath):
    """一次读取计算 SHA-1 和 SHA-256，返回 (sha1_hex, sha256_hex)。"""
    h1 = hashlib.sha1()
    h256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h1.update(chunk)
            h256.update(chunk)
    return h1.hexdigest(), h256.hexdigest()

def _build_model_response(repo_id: str, revision: Optional[str] = None) -> dict:
    repo_path = os.path.join(FAKE_HUB_ROOT, repo_id)
    if not os.path.isdir(repo_path):
        raise HTTPException(status_code=404, detail="Repository not found")

    # siblings: align with hf-mirror (only rfilename)
    siblings = []
    for root, _, files in os.walk(repo_path):
        for fname in files:
            abs_path = os.path.join(root, fname)
            rel_path = os.path.relpath(abs_path, repo_path)
            siblings.append({"rfilename": rel_path.replace(os.sep, "/")})
    # Keep deterministic order
    siblings = sorted(siblings, key=lambda x: x["rfilename"])

    fake_sha = f"fakesha-{revision}" if revision else "fakesha1234567890"

    # Populate fields to match hf-mirror schema (types only need to match)
    total_size = 0
    for r, _, files in os.walk(repo_path):
        for f in files:
            total_size += get_file_size(os.path.join(r, f))

    response_data = {
        "_id": f"local/{repo_id}",
        "id": repo_id,
        "private": False,
        "pipeline_tag": "text-generation",
        "library_name": "transformers",
        "tags": ["transformers", "gpt2", "text-generation"],
        "downloads": 0,
        "likes": 0,
        "modelId": repo_id,
        "author": "local-user",
        "sha": fake_sha,
        "lastModified": "1970-01-01T00:00:00.000Z",
        "createdAt": "1970-01-01T00:00:00.000Z",
        "gated": False,
        "disabled": False,
        "widgetData": [{"text": "Hello"}],
        "model-index": None,
        "config": {"architectures": ["GPT2LMHeadModel"], "model_type": "gpt2", "tokenizer_config": {}},
        "cardData": {"language": "en", "tags": ["example"], "license": "mit"},
        "transformersInfo": {
            "auto_model": "AutoModelForCausalLM",
            "pipeline_tag": "text-generation",
            "processor": "AutoTokenizer",
        },
        "safetensors": {"parameters": {"F32": 0}, "total": 0},
        "siblings": siblings,
        "spaces": [],
        "usedStorage": int(total_size),
    }
    return response_data


@app.on_event("startup")
async def _print_startup_info():
    # Helpful diagnostics to confirm where the server looks for repos
    try:
        root_abs = os.path.abspath(FAKE_HUB_ROOT)
    except Exception:
        root_abs = FAKE_HUB_ROOT
    print(f"[fake-hub] FAKE_HUB_ROOT = {root_abs}")


## (model endpoints registered later to ensure specific routes take precedence)

def _build_dataset_response(repo_id: str, revision: Optional[str] = None) -> dict:
    """Build a dataset info response aligned (by types) with hf-mirror datasets API.

    Note: Datasets commonly include nested files (e.g., data/train-00000.csv). We include
    only the 'rfilename' field for each file, recursively discovered from the repo root.
    """
    # Map dataset ID to on-disk path under fake hub. We keep datasets under
    # fake_hub/datasets/<namespace>/<name>
    ds_path = os.path.join(FAKE_HUB_ROOT, "datasets", repo_id)
    if not os.path.isdir(ds_path):
        raise HTTPException(status_code=404, detail="Dataset not found")

    # Recursively collect relative file paths as siblings
    siblings = []
    for root, _, files in os.walk(ds_path):
        for fname in files:
            abs_path = os.path.join(root, fname)
            rel_path = os.path.relpath(abs_path, ds_path)
            siblings.append({"rfilename": rel_path.replace(os.sep, "/")})

    fake_sha = f"fakesha-{revision}" if revision else "fakesha1234567890"

    # Compute used storage as sum of sizes
    total_size = 0
    for root, _, files in os.walk(ds_path):
        for fname in files:
            total_size += get_file_size(os.path.join(root, fname))

    # Minimal set of fields that mirrors common dataset API keys (types match).
    # We intentionally avoid model-specific fields.
    response_data = {
        "_id": f"local/datasets/{repo_id}",
        "id": repo_id,
        "private": False,
        "tags": ["dataset"],
        "downloads": 0,
        "likes": 0,
        "author": "local-user",
        "sha": fake_sha,
        "lastModified": "1970-01-01T00:00:00.000Z",
        "createdAt": "1970-01-01T00:00:00.000Z",
        "gated": False,
        "disabled": False,
        "cardData": {"license": "mit", "language": ["en"]},
        "siblings": sorted(siblings, key=lambda x: x["rfilename"]),
        "usedStorage": int(total_size),
    }
    return response_data


@app.get("/api/datasets/{repo_id:path}/revision/{revision}")
async def get_dataset_info_at_revision(repo_id: str, revision: str, request: Request):
    return JSONResponse(content=_build_dataset_response(repo_id, revision))


@app.post("/api/datasets/{repo_id:path}/paths-info/{revision}")
async def get_dataset_paths_info(repo_id: str, revision: str, request: Request):
    """Minimal implementation of the Hub "paths-info" endpoint for datasets.

    Accepts an optional JSON body: {"paths": ["path1", "path2", ...], "expand": true}
    - If paths omitted or empty: returns info for all files under the dataset root.
    - For each path that is a directory: if expand is true (default), returns all files
      under that directory; otherwise returns the directory entry only.
    - For files: returns a single record with size.
    """
    ds_path = os.path.join(FAKE_HUB_ROOT, "datasets", repo_id)
    if not os.path.isdir(ds_path):
        raise HTTPException(status_code=404, detail="Dataset not found")

    # Parse body (gracefully handle empty/no body)
    try:
        body = await request.json()
    except Exception:
        body = None

    paths = []
    expand = True
    if isinstance(body, dict):
        p = body.get("paths")
        if isinstance(p, list):
            # Keep only strings
            paths = [str(x) for x in p if isinstance(x, str)]
        e = body.get("expand")
        if isinstance(e, bool):
            expand = e

    results: list[dict] = []
    if not paths:
        results = _collect_paths_info(ds_path)
    else:
        for p in paths:
            # Normalize: treat empty string or "/" as root
            if p.strip() in {"", "/", "."}:
                if expand:
                    results.extend(_collect_paths_info(ds_path))
                else:
                    results.append({"path": "", "type": "directory"})
                continue
            if expand:
                results.extend(_collect_paths_info(ds_path, p))
            else:
                # Only return the path itself (file or dir), but ensure files include oid/lfs.
                norm_rel = p.strip().lstrip("/")
                abs_target = os.path.abspath(os.path.join(ds_path, norm_rel))
                if abs_target.startswith(os.path.abspath(ds_path) + os.sep) or abs_target == os.path.abspath(ds_path):
                    if os.path.isdir(abs_target):
                        results.append({"path": norm_rel.replace(os.sep, "/"), "type": "directory"})
                    elif os.path.isfile(abs_target):
                        # Reuse _collect_paths_info to include size and oid/lfs when possible
                        file_infos = _collect_paths_info(ds_path, norm_rel)
                        # Expect exactly one file entry; add if present
                        for it in file_infos:
                            if it.get("type") == "file":
                                results.append(it)
                                break

    # Deduplicate results by (path, type)
    seen = set()
    unique: list[dict] = []
    for it in results:
        key = (it.get("path", ""), it.get("type", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    return JSONResponse(content=unique)


@app.get("/api/datasets/{repo_id:path}")
async def get_dataset_info(repo_id: str, request: Request):
    return JSONResponse(content=_build_dataset_response(repo_id))


def _collect_paths_info(base_dir: str, rel_prefix: str | None = None) -> list[dict]:
    """Recursively collect file entries under base_dir as path-info records.

    Returns a list of dicts with keys: path, type ("file"|"directory"), and size (for files).
    If rel_prefix is given, only items under that relative path are returned and paths are
    expressed relative to base_dir (posix style).
    """
    # Normalize inputs
    base_dir = os.path.abspath(base_dir)
    results: list[dict] = []

    # Optional: load precomputed sidecar to avoid hashing large files
    sidecar_map: dict[str, dict] = {}
    try:
        import json
        sidecar_path = os.path.join(base_dir, ".paths-info.json")
        if os.path.isfile(sidecar_path):
            with open(sidecar_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ents = data.get("entries") if isinstance(data, dict) else None
            if isinstance(ents, list):
                for it in ents:
                    if isinstance(it, dict) and it.get("type") == "file" and isinstance(it.get("path"), str):
                        sidecar_map[it["path"]] = it
    except Exception:
        # Sidecar optional; ignore errors
        sidecar_map = {}

    def add_directory(rel_path: str):
        results.append({
            "path": rel_path.replace(os.sep, "/"),
            "type": "directory",
        })

    def add_file(abs_path: str, rel_path: str):
        rel_norm = rel_path.replace(os.sep, "/")
        sc = sidecar_map.get(rel_norm)
        if sc is not None:
            # Prefer sidecar but ensure consistency with on-disk size and OIDs.
            rec = {"path": rel_norm, "type": "file"}
            actual_size = get_file_size(abs_path)
            rec["size"] = actual_size  # always report real file size to avoid confusion

            # Gather sidecar sizes for comparison
            sc_size = sc.get("size") if isinstance(sc.get("size"), int) else None
            sc_lfs_size = None
            if isinstance(sc.get("lfs"), dict) and isinstance(sc["lfs"].get("size"), int):
                sc_lfs_size = sc["lfs"]["size"]

            # Decide whether to trust sidecar hashes: only when sizes match
            sizes_known = [s for s in (sc_size, sc_lfs_size) if isinstance(s, int)]
            trust_sidecar_hashes = (len(sizes_known) > 0) and all(s == actual_size for s in sizes_known)

            lfs = {}
            if trust_sidecar_hashes:
                # Use sidecar hashes when sizes agree
                if isinstance(sc.get("oid"), str):
                    rec["oid"] = sc["oid"]
                if isinstance(sc.get("lfs"), dict) and isinstance(sc["lfs"].get("oid"), str):
                    lfs["oid"] = sc["lfs"]["oid"]
                # ensure lfs size reflects actual file size
                lfs["size"] = actual_size
                # If any oid missing, compute to satisfy clients expecting both
                need_sha1 = "oid" not in rec
                need_sha256 = "oid" not in lfs
                if need_sha1 or need_sha256:
                    sha1_hex, sha256_hex = get_file_hashes(abs_path)
                    if need_sha1:
                        rec["oid"] = sha1_hex
                    if need_sha256:
                        lfs["oid"] = f"sha256:{sha256_hex}"
            else:
                # Sidecar sizes absent or mismatched: recompute to avoid stale metadata
                sha1_hex, sha256_hex = get_file_hashes(abs_path)
                rec["oid"] = sha1_hex
                lfs["oid"] = f"sha256:{sha256_hex}"
                lfs["size"] = actual_size

            if lfs:
                rec["lfs"] = lfs
            results.append(rec)
            return

        # No sidecar: compute hashes on demand
        size = get_file_size(abs_path)
        sha1_hex, sha256_hex = get_file_hashes(abs_path)
        results.append({
            "path": rel_norm,
            "type": "file",
            "size": size,
            "oid": sha1_hex,
            "lfs": {"oid": f"sha256:{sha256_hex}", "size": size},
        })

    def walk_dir(root_abs: str, root_rel: str):
        # Ensure directory itself appears in the listing (except for empty root)
        if root_rel:
            add_directory(root_rel)
        for r, _, files in os.walk(root_abs):
            rel = os.path.relpath(r, base_dir)
            if rel == ".":
                rel = ""
            # Add files in this directory
            for fname in files:
                ap = os.path.join(r, fname)
                rp = os.path.join(rel, fname) if rel else fname
                add_file(ap, rp)

    if rel_prefix:
        # Sanitize and constrain under base_dir
        norm_rel = rel_prefix.strip().lstrip("/")
        abs_target = os.path.abspath(os.path.join(base_dir, norm_rel))
        if not abs_target.startswith(base_dir + os.sep) and abs_target != base_dir:
            # Outside of repo
            return results
        if os.path.isdir(abs_target):
            walk_dir(abs_target, norm_rel)
        elif os.path.isfile(abs_target):
            add_file(abs_target, norm_rel)
        # If path doesn't exist, return empty
        return results

    # No prefix provided: list everything recursively
    walk_dir(base_dir, "")
    return results


@app.post("/api/datasets/{repo_id:path}/paths-info/{revision}")
async def get_dataset_paths_info(repo_id: str, revision: str, request: Request):
    """Minimal implementation of the Hub "paths-info" endpoint for datasets.

    Accepts an optional JSON body: {"paths": ["path1", "path2", ...], "expand": true}
    - If paths omitted or empty: returns info for all files under the dataset root.
    - For each path that is a directory: if expand is true (default), returns all files
      under that directory; otherwise returns the directory entry only.
    - For files: returns a single record with size.
    """
    ds_path = os.path.join(FAKE_HUB_ROOT, "datasets", repo_id)
    if not os.path.isdir(ds_path):
        raise HTTPException(status_code=404, detail="Dataset not found")

    # Parse body (gracefully handle empty/no body)
    try:
        body = await request.json()
    except Exception:
        body = None

    paths = []
    expand = True
    if isinstance(body, dict):
        p = body.get("paths")
        if isinstance(p, list):
            # Keep only strings
            paths = [str(x) for x in p if isinstance(x, str)]
        e = body.get("expand")
        if isinstance(e, bool):
            expand = e

    results: list[dict] = []
    if not paths:
        results = _collect_paths_info(ds_path)
    else:
        for p in paths:
            # Normalize: treat empty string or "/" as root
            if p.strip() in {"", "/", "."}:
                if expand:
                    results.extend(_collect_paths_info(ds_path))
                else:
                    results.append({"path": "", "type": "directory"})
                continue
            if expand:
                results.extend(_collect_paths_info(ds_path, p))
            else:
                # Only return the path itself (file or dir)
                norm_rel = p.strip().lstrip("/")
                abs_target = os.path.abspath(os.path.join(ds_path, norm_rel))
                if abs_target.startswith(os.path.abspath(ds_path) + os.sep) or abs_target == os.path.abspath(ds_path):
                    if os.path.isdir(abs_target):
                        results.append({"path": norm_rel.replace(os.sep, "/"), "type": "directory"})
                    elif os.path.isfile(abs_target):
                        results.append({
                            "path": norm_rel.replace(os.sep, "/"),
                            "type": "file",
                            "size": get_file_size(abs_target),
                        })

    seen = set()
    unique: list[dict] = []
    for it in results:
        key = (it.get("path", ""), it.get("type", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    return JSONResponse(content=unique)


@app.post("/api/models/{repo_id:path}/paths-info/{revision}")
async def get_model_paths_info(repo_id: str, revision: str, request: Request):
    """Minimal implementation of the Hub "paths-info" endpoint for models.

    Accepts an optional JSON body: {"paths": ["path1", "path2", ...], "expand": true}
    Behavior mirrors the dataset variant but rooted at fake_hub/<repo_id>.
    """
    repo_path = os.path.join(FAKE_HUB_ROOT, repo_id)
    if not os.path.isdir(repo_path):
        raise HTTPException(status_code=404, detail="Repository not found")

    try:
        body = await request.json()
    except Exception:
        body = None

    paths = []
    expand = True
    if isinstance(body, dict):
        p = body.get("paths")
        if isinstance(p, list):
            paths = [str(x) for x in p if isinstance(x, str)]
        e = body.get("expand")
        if isinstance(e, bool):
            expand = e

    results: list[dict] = []
    if not paths:
        results = _collect_paths_info(repo_path)
    else:
        for p in paths:
            if p.strip() in {"", "/", "."}:
                if expand:
                    results.extend(_collect_paths_info(repo_path))
                else:
                    results.append({"path": "", "type": "directory"})
                continue
            if expand:
                results.extend(_collect_paths_info(repo_path, p))
            else:
                norm_rel = p.strip().lstrip("/")
                abs_target = os.path.abspath(os.path.join(repo_path, norm_rel))
                if abs_target.startswith(os.path.abspath(repo_path) + os.sep) or abs_target == os.path.abspath(repo_path):
                    if os.path.isdir(abs_target):
                        results.append({"path": norm_rel.replace(os.sep, "/"), "type": "directory"})
                    elif os.path.isfile(abs_target):
                        # Reuse _collect_paths_info to include size and oid/lfs when possible
                        file_infos = _collect_paths_info(repo_path, norm_rel)
                        for it in file_infos:
                            if it.get("type") == "file":
                                results.append(it)
                                break

    seen = set()
    unique: list[dict] = []
    for it in results:
        key = (it.get("path", ""), it.get("type", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    return JSONResponse(content=unique)


@app.get("/api/models/{repo_id:path}/revision/{revision}")
async def get_model_info_at_revision(repo_id: str, revision: str, request: Request):
    """
    Compatibility endpoint: same payload as /api/models/{repo_id} but scoped to a revision.
    The current implementation ignores the revision and serves the latest files.
    """
    return JSONResponse(content=_build_model_response(repo_id, revision))


@app.get("/api/models/{repo_id:path}")
async def get_model_info(repo_id: str, request: Request):
    """
    模拟 Hugging Face Hub 的模型信息 API（对齐 hf-mirror 结构）。
    """
    return JSONResponse(content=_build_model_response(repo_id))


@app.api_route("/{repo_id:path}/resolve/{revision}/{filename:path}", methods=["GET", "HEAD"])
async def resolve_file_download(repo_id: str, revision: str, filename: str, request: Request):
    """
    这个路由是实际提供文件下载的地方。
    `huggingface-cli` 会根据上一个 API 返回的 'rfilename' 访问这个地址。
    """
    filepath = os.path.join(FAKE_HUB_ROOT, repo_id, filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")

    # 对 HEAD 请求返回元数据头，便于 huggingface 客户端探测
    if request.method == "HEAD":
        size = get_file_size(filepath)
        sha256 = get_file_sha256(filepath)
        headers = {
            "Content-Length": str(size),
            "Content-Type": "application/octet-stream",
            "Accept-Ranges": "bytes",
            # 以下为兼容性头部，供客户端识别和缓存
            "ETag": f'"{sha256}"',
            "x-repo-commit": revision,
            "x-revision": revision,
        }
        if filename.endswith(".bin"):
            headers["x-lfs-size"] = str(size)
        return Response(status_code=200, headers=headers)

    # GET 请求返回文件内容；支持 Range 请求（bytes=...）
    range_header = request.headers.get("range") or request.headers.get("Range")
    if range_header:
        size = get_file_size(filepath)

        def parse_range(h: str, total: int):
            try:
                unit, rng = h.strip().split("=", 1)
            except ValueError:
                return None
            if unit.lower() != "bytes":
                return None
            # 仅处理第一段
            first = rng.split(",", 1)[0].strip()
            if "-" not in first:
                return None
            start_s, end_s = first.split("-", 1)
            if start_s == "":
                # 后缀：bytes=-N 表示最后 N 字节
                try:
                    n = int(end_s)
                except Exception:
                    return None
                if n <= 0:
                    return None
                start = max(total - n, 0)
                end = total - 1 if total > 0 else 0
            else:
                try:
                    start = int(start_s)
                except Exception:
                    return None
                if end_s == "":
                    end = total - 1
                else:
                    try:
                        end = int(end_s)
                    except Exception:
                        return None
            # 规范化与校验
            if start < 0:
                return None
            if start >= total:
                # 不可满足
                return (None, None)
            if end >= total:
                end = total - 1
            if end < start:
                return (None, None)
            return (start, end)

        rng = parse_range(range_header, size)
        if rng is None:
            # 无效 Range 头，按规范可忽略并返回 200；这里选择忽略 Range。
            return FileResponse(
                filepath,
                filename=os.path.basename(filename),
                media_type="application/octet-stream",
            )
        start, end = rng
        if start is None or end is None:
            # 不可满足
            return Response(
                status_code=416,
                headers={
                    "Content-Range": f"bytes */{size}",
                    "Accept-Ranges": "bytes",
                },
            )

        length = end - start + 1

        def iter_file(path: str, offset: int, count: int, chunk_size: int = 8192):
            with open(path, "rb") as f:
                f.seek(offset)
                remaining = count
                while remaining > 0:
                    chunk = f.read(min(chunk_size, remaining))
                    if not chunk:
                        break
                    yield chunk
                    remaining -= len(chunk)

        headers = {
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Type": "application/octet-stream",
            "x-repo-commit": revision,
            "x-revision": revision,
        }

        return StreamingResponse(
            iter_file(filepath, start, length),
            status_code=206,
            media_type="application/octet-stream",
            headers=headers,
        )

    # 常规整文件响应
    return FileResponse(filepath, filename=os.path.basename(filename), media_type="application/octet-stream")


if __name__ == "__main__":
    print(f"模型仓库根目录: {os.path.abspath(FAKE_HUB_ROOT)}")
    uvicorn.run(app, host="0.0.0.0", port=8000)
