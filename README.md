好的，这是一个为这个项目准备的 `README.md` 文件。你可以将它保存在项目的根目录下。

---

# Fake Hugging Face Hub (伪 Hugging Face Hub 服务)

这是一个使用 FastAPI 构建的简单 Web 服务，旨在模拟 [Hugging Face Hub](https://huggingface.co/) 的核心功能。可作为本地、离线或私有的模型/数据集仓库，并与官方下载客户端保持兼容。API 结构对齐 `hf-mirror.com`，支持新版 `hf` 客户端的 `HEAD` 探测、`Range` 分段与修订查询。


## ✨ 功能特性

*   **本地模型/数据集托管**: 在你自己的服务器上托管 Hugging Face 模型与数据集。
*   **CLI 兼容**: 支持新版 `hf download`（推荐）与旧版 `huggingface-cli download`（将提示已弃用）。
*   **API 对齐**:
    - 模型：`/api/models/{repo_id}` 与 `/api/models/{repo_id}/revision/{revision}` 返回字段集合与类型对齐 `hf-mirror.com`；`siblings` 仅包含 `rfilename`。
    - 数据集：`/api/datasets/{dataset_id}` 与 `/api/datasets/{dataset_id}/revision/{revision}` 返回常见字段（`id`、`sha`、`lastModified`、`cardData`、`siblings` 等）。
    - 路径信息：兼容 `paths-info` 接口，供客户端批量查询路径元数据：
      - 模型：`POST /api/models/{repo_id}/paths-info/{revision}`
      - 数据集：`POST /api/datasets/{repo_id}/paths-info/{revision}`
      - 请求体示例：`{"paths": ["", "subdir/"], "expand": true}`；响应为若干 `{path, type, size?}` 条目。
*   **Range/HEAD 支持**: 下载路由支持标准 `Range`（`bytes=`）与 `HEAD`：
    - `HEAD` 返回 `Content-Length`、`Content-Type`、`Accept-Ranges`、`ETag` 等关键头部。
    - `GET` 解析 `Range: bytes=...`，返回 `206 Partial Content` 与 `Content-Range`，不满足时返回 `416`。
*   **轻量级**: 基于 FastAPI 构建，性能高且易于部署。
*   **简单的仓库结构**: 只需将模型文件放在相应的文件夹中，服务会自动发现它们。
*   **易于扩展**: 可以轻松添加更多路由或功能来满足特定需求。

## ⚙️ 环境准备

在开始之前，请确保你已经安装了以下工具：

*   Python 3.12+
*   pip
*   `huggingface-hub` Python 库（包含 `huggingface-cli`；新版还提供 `hf` 命令）

如果你还没有安装 `huggingface-hub`，可以通过以下命令安装：
```bash
pip install huggingface-hub
```

## 🚀 安装与设置

1.  **克隆或创建项目文件**:
    将 `main.py` 和 `fake_hub` 目录放在你的项目根目录下。

2.  **创建项目结构**:
    你的项目目录应该看起来像这样：

    ```
    .
    ├── fake_hub/                             # 仓库根目录
    │   ├── gpt2/                             # 模型仓库 (例如 'gpt2')
    │   │   ├── config.json
    │   │   ├── pytorch_model.bin
    │   │   └── .gitattributes
    │   └── datasets/
    │       └── HuggingFaceFW/finepdfs/       # 数据集仓库 (命名空间/名称)
    │           ├── README.md
    │           ├── dataset_infos.json
    │           └── data/
    │               └── sample.jsonl
    ├── main.py                  # FastAPI 服务代码
    └── README.md                # 本文档
    ```

3.  **安装依赖**:
    项目使用uv管理依赖
    ```bash
    uv sync
    ```

## ▶️ 如何使用

1.  **启动服务**:
    在项目根目录下，运行 `main.py` 来启动 FastAPI 服务。

    ```bash
    uv run python -m uvicorn main:app --reload

    # 局域网/容器访问（监听 0.0.0.0:8000）
    uv run python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
    ```

    服务启动后，你将看到类似以下的输出，表明服务正在 `http://127.0.0.1:8000` 上运行：
    ```
    Fake Hugging Face Hub 正在运行在 http://127.0.0.1:8000
    [fake-hub] FAKE_HUB_ROOT = /path/to/your/project/fake_hub
    你可以通过设置 HF_ENDPOINT 环境变量来从本服务下载:

      export HF_ENDPOINT=http://127.0.0.1:8000
      huggingface-cli download gpt2
    ```

    可选：请求详细日志（可关联 Request-ID）
    - 服务记录：`method path?query`、client IP:port、协议版本、scheme、处理时长；并打印请求与响应头。
    - 默认会记录任意 Content-Type 的请求体前 `LOG_BODY_MAX` 字节（UTF-8 解码，无法解码处替换）。
    - 每条请求日志带 `X-Request-ID`，同时写入响应头便于排查链路。
    - 环境变量控制：
      ```bash
      export LOG_REQUESTS=1        # 1/true 开启（默认），0 关闭
      export LOG_BODY_MAX=4096     # 请求体最大记录字节数（默认 4096）
      export LOG_HEADERS=all       # all|minimal（默认 all，记录全部请求头）
      export LOG_RESP_HEADERS=1    # 记录响应头（默认 1）
      export LOG_REDACT=1          # 脱敏 Authorization/Cookie 等（默认 1）
      export LOG_BODY_ALL=1        # 所有 Content-Type 都尝试记录体（默认 1）
      ```

2.  **配置客户端**:
    打开一个新的终端窗口，设置 `HF_ENDPOINT` 环境变量，将客户端指向你的本地服务。

    ```bash
    export HF_ENDPOINT=http://127.0.0.1:8000
    ```
    **注意**: 这个环境变量只在当前的终端会话中有效。

3.  **下载模型/数据集**:
    现在，你可以像从官方 Hub 下载一样使用客户端。

    *   **下载整个仓库（推荐）**:
        ```bash
        hf download gpt2 --local-dir ./downloaded_gpt2
        ```

    *   **旧版命令（会提示已弃用）**:
        ```bash
        huggingface-cli download gpt2 --local-dir ./downloaded_gpt2
        ```

    *   **下载特定文件**:
        ```bash
        hf download gpt2 --include config.json --local-dir ./downloaded_gpt2
        ```

    *   **下载数据集（示例）**:
        ```bash
        hf download --repo-type dataset "HuggingFaceFW/finepdfs" --local-dir ./downloaded_finepdfs
        ```
    *   **组织名/仓库名**：模型与数据集的 `repo_id` 支持组织名前缀（如 `openai/gpt-oss-20b`）。

    *   **查看下载流量**:
        当你执行下载命令时，可以在运行 FastAPI 服务的终端中看到访问日志，例如：
        ```
        INFO:     127.0.0.1:xxxxx - "GET /api/models/gpt2 HTTP/1.1" 200 OK
        INFO:     127.0.0.1:xxxxx - "GET /api/models/gpt2/revision/main HTTP/1.1" 200 OK
        INFO:     127.0.0.1:xxxxx - "GET /gpt2/resolve/main/config.json HTTP/1.1" 200 OK
        INFO:     127.0.0.1:xxxxx - "GET /gpt2/resolve/main/pytorch_model.bin HTTP/1.1" 200 OK
        INFO:     127.0.0.1:xxxxx - "GET /api/datasets/HuggingFaceFW/finepdfs HTTP/1.1" 200 OK
        INFO:     127.0.0.1:xxxxx - "GET /datasets/HuggingFaceFW/finepdfs/resolve/main/README.md HTTP/1.1" 200 OK
        INFO:     127.0.0.1:xxxxx - "GET /datasets/HuggingFaceFW/finepdfs/resolve/main/data/sample.jsonl HTTP/1.1" 200 OK
        INFO:     127.0.0.1:xxxxx - "POST /api/models/openai/gpt-oss-20b/paths-info/fakesha-main HTTP/1.1" 200 OK
        ...
        ```

## 🧰 一键骨架克隆（只复制文件结构）

当你想快速在本地搭一个“与真实仓库同结构”的假 repo（仅目录与文件名，不含真实内容）时，可以使用内置的 CLI：

```bash
# 方式 A（推荐，免安装本地包）：使用模块运行
uv run python -m skeleton gpt2 --repo-type model
uv run python -m skeleton HuggingFaceFW/finepdfs --repo-type dataset

# 方式 B（安装可执行脚本后使用）
uv pip install -e .
uv run fakehub-skeleton gpt2 --repo-type model

# 常用参数
uv run python -m skeleton <repo_id> --repo-type {model|dataset} \
  --revision main \
  --endpoint https://huggingface.co \
  --include "*.json" --include "*.md" \
  --exclude "*.bin" \
  --max-files 100 \
  --dst ./fake_hub/custom_root \
  --force \
  --fill --fill-size 16MiB --fill-content "FAKE"

uv run python -m skeleton tencent/HunyuanImage-2.1 --repo-type model --fill --fill-size 1024MiB --fill-content "FAKE"
```

说明：
- 命令会从远端 API 获取仓库的树结构，创建空文件，或按需用固定内容填充（`--fill`）。
- 默认输出路径遵循服务约定：模型在 `fake_hub/<repo_id>`，数据集在 `fake_hub/datasets/<namespace>/<name>`。
- 通过 `--endpoint` 可切换到镜像站，例如 `https://hf-mirror.com`。
- 使用 `--include/--exclude` 进行文件选择；`--max-files` 可限制数量。
- 不再支持“从本地目录生成骨架”的模式。

### 预生成 LFS/OID 元数据（与本地文件真实一致）

- skeleton CLI 在创建占位文件后，会基于“磁盘上的实际文件”计算哈希并生成 `/.paths-info.json`，每个文件项包含：
  - `size`: 实际文件大小
  - `oid`: 文件内容的 SHA‑1
  - `lfs.oid`: 形如 `sha256:<hex>` 的 SHA‑256 值
  - `lfs.size`: 实际文件大小
- 服务端在处理 `paths-info` 时会优先读取 sidecar，并仅在 sidecar 的 `size` 与磁盘实际大小一致时信任其中的哈希；若缺失或不一致，则即时重新计算，确保对客户端给出的元数据准确无误。
- 无论是否提供 `paths` 或 `expand`，`paths-info` 响应中每个文件都会包含 `oid`（或在 `lfs` 子对象中携带 `oid`）。

### 填充文件内容（可选）

- `--fill`: 将创建的文件用重复内容填充（默认为空文件）。
- `--fill-size`: 每个文件填充的大小，支持 `B/KB/MB/GB` 或 `KiB/MiB/GiB`，如 `16MiB`。未指定时默认 `16MiB`。
- `--fill-content`: 用于重复填充的字符串（UTF-8 编码），未指定时以 0 字节填充。

---

## 🧰 准备本地仓库结构

将需要暴露给客户端的模型或数据集文件直接放入以下目录结构中：

```
fake_hub/
  <model_repo_id>/
    config.json
    *.bin
    subdirs/...（可选）
  datasets/
    <namespace>/<dataset_name>/
      README.md
      data/...（可选）
```

说明：
- 模型位于 `fake_hub/<repo_id>`；数据集位于 `fake_hub/datasets/<namespace>/<name>`。
- 服务会递归发现子目录中文件并在 `siblings`（仅 `rfilename`）与 `paths-info` 中返回。
- 如仓库根存在 `/.paths-info.json`，服务端将优先使用且校验其 `size` 与实际文件一致；不一致时将忽略并重新计算哈希。

## 🔧 工作原理

客户端下载流程大致如下：

1.  客户端请求 `{HF_ENDPOINT}/api/models/{repo_id}` 或 `{HF_ENDPOINT}/api/datasets/{dataset_id}` 获取文件清单；部分版本会继续请求 `{HF_ENDPOINT}/api/.../revision/{revision}`（当前实现会忽略 `revision`）。
    - 某些客户端会调用 `POST /api/(models|datasets)/{repo_id}/paths-info/{revision}` 查询路径元信息（含 `size`）。本服务已实现最小子集以满足新版 `hf` 的调用。
2.  服务扫描 `fake_hub/{repo_id}` 目录，返回与 `hf-mirror.com` 结构一致的 JSON（如 `_id`、`id`、`modelId`、`sha`、`tags`、`siblings` 等；其中 `siblings` 仅含 `rfilename`）。
3.  客户端基于响应构造下载 URL：
    - 模型：`{HF_ENDPOINT}/{repo_id}/resolve/{revision}/{filename}`
    - 数据集：`{HF_ENDPOINT}/datasets/{dataset_id}/resolve/{revision}/{filename}`（支持子路径 `filename`）
4.  服务通过 `/{repo_id:path}/resolve/{revision}/{filename:path}` 路由将对应文件以 `FileResponse` 返回（`filename` 支持子目录）。

## ➕ 添加更多模型

添加新的模型非常简单：

1.  在 `fake_hub` 目录下，创建一个新的文件夹，文件夹名称就是你的模型 `repo_id`。例如，要添加 `bert-base-uncased`，就创建一个名为 `bert-base-uncased` 的文件夹。
    ```bash
    mkdir fake_hub/bert-base-uncased
    ```
2.  将该模型的所有文件（`config.json`, `pytorch_model.bin` 等）复制到这个新创建的文件夹中。
3.  **无需重启服务**。FastAPI 服务会在下次收到请求时自动发现这些新文件。

现在你就可以通过 `hf download bert-base-uncased --local-dir ./downloaded_bert` 来下载你的新模型了。

## 🧪 自检与排错

- 快速检查 API：
  ```bash
  curl -s $HF_ENDPOINT/api/models/gpt2 | head -c 200; echo
  curl -s $HF_ENDPOINT/api/models/gpt2/revision/main | head -c 200; echo
  # Range 分段下载验证（前 10 字节）
  curl -s -H 'Range: bytes=0-9' -i $HF_ENDPOINT/gpt2/resolve/main/config.json | sed -n '1,10p'
  ```
- 路由 404：确认使用 `{repo_id:path}` 以支持组织名（如 `openai/gpt-oss-20b`），并确保 `FAKE_HUB_ROOT` 指向正确根（启动日志会打印）。
- 下载为空/失败：确认模型文件位于 `fake_hub/gpt2/`（例如 `config.json`, `pytorch_model.bin`）。
- 可选：限定缓存目录，避免污染全局缓存：
  ```bash
  export HF_HOME=$PWD/.hf_home
  ```

附：快速检查 `paths-info` 与 Range
```bash
# 路径信息
curl -s -X POST "$HF_ENDPOINT/api/models/gpt2/paths-info/fakesha-main" -H 'content-type: application/json' -d '{"paths":[""],"expand":true}' | head -c 200; echo
curl -s -X POST "$HF_ENDPOINT/api/datasets/HuggingFaceFW/finepdfs/paths-info/fakesha-main" -H 'content-type: application/json' -d '{}'

# Range 分段验证
curl -i -H 'Range: bytes=0-9' "$HF_ENDPOINT/gpt2/resolve/main/config.json" | sed -n '1,15p'
```

## ✅ 集成测试

本仓库提供与 `hf-mirror.com` 的结构对齐测试：

```bash
uv run pytest -vs tests/test_api_compat.py
```

测试内容：
- 对比 `GET /api/models/gpt2` 与 `GET /api/models/gpt2/revision/main` 的字段集合与类型。
- 校验文件路由 `HEAD/GET` 行为与必要响应头。
- 404/4xx 异常路径一致性（镜像站可能返回 401/404，已做兼容断言）。

另外提供数据集兼容性测试（对 `HuggingFaceFW/finepdfs`）：

```bash
uv run pytest -vs tests/test_dataset_api_compat.py
```

测试内容：
- `GET /api/datasets/...` 与 `GET /api/datasets/.../revision/main` 本地字段应为远端字段子集，且类型一致。
- 校验 `HEAD/GET` 行为与必要响应头，包含子路径文件（如 `data/*.jsonl`）。
- 404/4xx 异常路径一致性。

## 📜 许可证

本项目采用 [MIT 许可证](https://opensource.org/licenses/MIT)。
