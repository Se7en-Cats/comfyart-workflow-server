"""builtin 工作流的本机路径适配层。

上游 `workflows/*.api.json` 里的模型文件名是作者环境的写法（经常没有子目录前缀，
如 `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors`），直接在本机跑会报
"Value not in list"。文件落到 `data/builtin` 后，用 ComfyUI `/object_info` 的实时枚举
做一次适配：某条引用的 basename 在枚举里存在且唯一时，替换成枚举里的完整值。

原则：
- 只做 basename **唯一**匹配，多个候选（不同子目录同名）时不动，绝不猜测；
- 真缺文件（枚举里连 basename 都没有）保持原样，交给模型依赖面板的下载/一键匹配；
- 只作用于 `data/builtin`，不碰 `data/workflows`；
- 适配结果记录到 `data/builtin/.adapted.json`（{相对路径: {upstream_blob, adapted_sha}}），
  art_updater 检查更新时据此把「本地=上游的已适配版本」判为一致，不误报、不覆盖。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import models
import store

ADAPT_STATE_FILE = store.BUILTIN_DIR / ".adapted.json"
COMFY_URL = __import__("os").environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
_backup_stamp: str | None = None


def _basename(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1].lower()


def fetch_registry(timeout: float = 30.0) -> dict[str, list[str]] | None:
    """拉 ComfyUI /object_info 构造 {kind: [枚举值]}；不可达返回 None。

    本机地址会按候选端口探测（ComfyUI Desktop 重启会换端口）。
    """
    import urllib.request

    from comfy import LOCAL_CANDIDATES as _CANDIDATES
    urls = [COMFY_URL] + [c for c in _CANDIDATES if c != COMFY_URL]
    for base in urls:
        try:
            with urllib.request.urlopen(f"{base}/object_info", timeout=timeout) as resp:
                info = json.loads(resp.read().decode("utf-8"))
                return models.build_registry(info)
        except Exception:
            continue
    return None


def adapt_graph(graph: dict, registry: dict[str, list[str]]) -> list[dict]:
    """把模型引用替换成枚举里 basename 唯一匹配的完整值。返回改动列表。

    不改：引用值已精确存在、basename 不存在、或 basename 在多个子目录里同名。
    """
    counts: dict[str, dict[str, int]] = {}
    first: dict[str, dict[str, str]] = {}
    for kind, values in registry.items():
        c = counts.setdefault(kind, {})
        f = first.setdefault(kind, {})
        for v in values:
            b = _basename(v)
            c[b] = c.get(b, 0) + 1
            f.setdefault(b, v)

    changes: list[dict] = []
    for ref in models.scan_models(graph):
        kind = ref["kind"]
        if kind not in counts:
            continue
        cur = ref["current"]
        if cur in registry.get(kind, []):
            continue  # 引用值已精确存在
        b = _basename(cur)
        if counts[kind].get(b, 0) == 1:
            target = first[kind][b]
            if target != cur:
                graph[ref["node_id"]]["inputs"][ref["field"]] = target
                changes.append({
                    "node_id": ref["node_id"],
                    "class_type": ref["class_type"],
                    "field": ref["field"],
                    "kind": kind,
                    "from": cur,
                    "to": target,
                })
    return changes


def adapt_file(rel: str, registry: dict[str, list[str]]) -> list[dict]:
    """适配 builtin 下的单个相对路径文件（有改动才写盘，写前备份到 .backup/adapt/）。"""
    global _backup_stamp
    p = store.BUILTIN_DIR / rel
    if not p.exists():
        return []
    graph = json.loads(p.read_text(encoding="utf-8"))
    changes = adapt_graph(graph, registry)
    if changes:
        if _backup_stamp is None:
            _backup_stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = store.BUILTIN_DIR / ".backup" / "adapt" / _backup_stamp / rel
        bak.parent.mkdir(parents=True, exist_ok=True)
        bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        p.write_text(json.dumps(graph, indent=2, ensure_ascii=False),
                     encoding="utf-8")
    return changes


def _git_blob_sha(data: bytes) -> str:
    """与 art_updater._git_blob_sha 一致的 git blob SHA。"""
    h = hashlib.sha1()
    h.update(b"blob " + str(len(data)).encode("ascii") + b"\0")
    h.update(data)
    return h.hexdigest()


def _load_state() -> dict:
    try:
        data = json.loads(ADAPT_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        ADAPT_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_adapt_state() -> dict:
    return _load_state()


def record_adapted(rel: str, upstream_blob: str | None) -> None:
    """记录 rel 的「已适配」状态：当前文件 SHA + 对应的上游 blob SHA。"""
    p = store.BUILTIN_DIR / rel
    if not p.exists():
        return
    state = _load_state()
    state[rel] = {
        "upstream_blob": upstream_blob,
        "adapted_sha": _git_blob_sha(p.read_bytes()),
    }
    _save_state(state)


def is_adapted(rel: str, upstream_blob: str | None, local_sha: str | None) -> bool:
    """本地文件是否就是「该上游 blob 的已适配版本」。"""
    rec = _load_state().get(rel)
    if not rec:
        return False
    if rec.get("upstream_blob") is None or upstream_blob is None:
        return False
    return (rec["upstream_blob"] == upstream_blob
            and rec.get("adapted_sha") == local_sha)
