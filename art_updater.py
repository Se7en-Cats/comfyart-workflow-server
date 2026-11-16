"""ComfyArt 上游内置工作流检查与同步。

同步范围：
  - `data/builtin/<action>.json`（17 个默认兜底）
  - `data/builtin/<action>/<上游文件名>`（VARIANT_MAP 里的内置变体）

不会碰：
  - 用户自定义工作流 `data/workflows/**`
  - `markers.py` / `actions.py` / 作者源码

检查策略：
  1. 启动时请求 GitHub 最新 commit；
  2. 用 git tree 的 blob SHA 与本地文件比对，不下载整包；
  3. 只有 blob 不同时才拉取对应 raw 文件，再做 JSON 语义比较；
  4. 有差异时把变更列表写到 data/art-update.json，前端显示提醒；
  5. 用户点「一键更新」时才写入 builtin，写入前备份到 .backup/<时间戳>/。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.request
from datetime import datetime, timezone
from typing import Any

import store
import local_adapt

REPO = "jtydhr88/ComfyArt"
BRANCH = "main"
COMMIT_API = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
TREE_API = f"https://api.github.com/repos/{REPO}/git/trees/{{sha}}?recursive=1"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/workflows/{{name}}"
USER_AGENT = "comfyart-workflow-server/1.0"

# action -> 上游 workflows/ 下的文件名
WORKFLOW_MAP = {
    "angles": "multiangle-qwen-image-2.1.api.json",
    "animate": "i2v-minimax-h3.api.json",
    "clone": "voice-clone-indextts.api.json",
    "cutout": "cutout-birefnet.api.json",
    "edit": "edit-qwen-image-2.1.api.json",
    "erase": "erase-lama.api.json",
    "expand": "outpaint-qwen-image-2.1.api.json",
    "generate": "qwen-image-2.1-t2i.api.json",
    "layers": "layers-qwen-image-layered.api.json",
    "model3d": "image-to-3d-trellis2.api.json",
    "mockup": "mockup-surface.api.json",
    "music": "music-minimax3.api.json",
    "refVideo": "r2v-minimax-h3.api.json",
    "sfx": "sfx-stable-audio.api.json",
    "speech": "speech-kokoro.api.json",
    "upscale": "upscale-ultrasharp-4x.api.json",
    "video": "t2v-minimax-h3.api.json",
    "views": "image-to-3d-pixal3d-multiview.api.json",
}
MAPPED_UPSTREAM = set(WORKFLOW_MAP.values())

# 槽位的内置变体：写到 data/builtin/<action>/<上游文件名>，与默认兜底并存。
# 未列入的（minimax-h3-image、qwen3tts 语音、transcribe）本机缺节点，暂不同步，
# 仍会出现在 unmapped 里提示。mockup-surface 已映射为新槽位 mockup（见 WORKFLOW_MAP）。
VARIANT_MAP: dict[str, list[str]] = {
    "generate": [
        "z-image-turbo-t2i.api.json",
        "krea2-turbo-t2i.api.json",
        "flux2-klein-9b-t2i.api.json",
        "flux3-klein-t2i.api.json",
        "moss-image-t2i.api.json",
        "qwen-image-2512-t2i.api.json",
        "ideogram4-t2i.api.json",
    ],
    "music": [
        "music-ace-step15.api.json",
        "music-yue2.api.json",
    ],
    "model3d": [
        "image-to-3d-pixal3d.api.json",
    ],
}
VARIANT_UPSTREAM = {name for names in VARIANT_MAP.values() for name in names}


def _targets() -> list[tuple[str, str, str]]:
    """(action, 上游文件名, 本地相对路径) —— 默认兜底 + 变体。"""
    targets = [(a, u, f"{a}.json") for a, u in WORKFLOW_MAP.items()]
    for action, names in VARIANT_MAP.items():
        for name in names:
            targets.append((action, name, f"{action}/{name}"))
    return targets

STATE_FILE = store.BASE / "art-update.json"
BACKUP_DIR = store.BUILTIN_DIR / ".backup"
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "checking": False,
    "updating": False,
    "update_available": False,
    "changed": [],
    "unmapped": [],
    "error": None,
    "checked_at": None,
    "upstream_sha": None,
    "upstream_date": None,
    "upstream_message": None,
    "upstream_blobs": {},
    "last_update_at": None,
}


def _load_state() -> None:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _STATE.update(data)
    except Exception:
        pass


_load_state()


def _save_state() -> None:
    try:
        STATE_FILE.write_text(
            json.dumps(_STATE, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def get_status() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def _opener():
    proxy = (
        os.environ.get("COMFYART_GITHUB_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
    )
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        return urllib.request.build_opener(handler)
    return urllib.request.build_opener()


def _read_url(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _opener().open(req, timeout=timeout) as resp:
        return resp.read()


def _latest_commit() -> dict[str, Any]:
    data = json.loads(_read_url(COMMIT_API, timeout=20).decode("utf-8"))
    return {
        "sha": data.get("sha"),
        "date": ((data.get("commit") or {}).get("committer") or {}).get("date"),
        "message": ((data.get("commit") or {}).get("message") or "").splitlines()[0],
    }


def _git_tree(sha: str) -> dict[str, str]:
    """返回 {path: blob_sha}，只保留 blob 类型。"""
    data = json.loads(_read_url(TREE_API.format(sha=sha), timeout=25).decode("utf-8"))
    tree = ((data.get("tree") or []))
    out: dict[str, str] = {}
    for item in tree:
        if item.get("type") == "blob" and item.get("path"):
            out[item["path"]] = item.get("sha", "")
    return out


def _git_blob_sha(data: bytes) -> str:
    """与 `git hash-object` 一致的 blob SHA（SHA-1）。"""
    h = hashlib.sha1()
    h.update(b"blob " + str(len(data)).encode("ascii") + b"\0")
    h.update(data)
    return h.hexdigest()


def _canonical(data: bytes) -> str | None:
    """按 JSON 语义比较，避免上游只改缩进/换行就误报更新。"""
    try:
        obj = json.loads(data.decode("utf-8"))
        return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return None


def _compare_with_tree(
    tree: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, bytes], list[str]]:
    """比较本地 builtin 与 git tree；返回 (changed, files_to_update, unmapped)。

    files_to_update 以本地相对路径（如 generate/z-image-turbo-t2i.api.json）为键。
    """
    changed: list[dict[str, Any]] = []
    files: dict[str, bytes] = {}
    known = MAPPED_UPSTREAM | VARIANT_UPSTREAM
    unmapped = sorted(
        os.path.basename(path)
        for path in tree
        if path.startswith("workflows/")
        and path.endswith(".api.json")
        and os.path.basename(path) not in known
    )

    for action, upstream, rel in _targets():
        remote_path = f"workflows/{upstream}"
        remote_blob = tree.get(remote_path)
        local_path = store.BUILTIN_DIR / rel

        if not remote_blob:
            changed.append({
                "action": action,
                "file": rel,
                "upstream": upstream,
                "reason": "上游文件不存在",
            })
            continue

        if not local_path.exists():
            data = _read_url(RAW_URL.format(name=upstream), timeout=20)
            files[rel] = data
            changed.append({
                "action": action,
                "file": rel,
                "upstream": upstream,
                "reason": "本地内置文件不存在",
                "upstream_bytes": data,
            })
            continue

        local_bytes = local_path.read_bytes()
        if _git_blob_sha(local_bytes) == remote_blob:
            continue

        # 本地是上游的「已适配」版本（本机路径补丁，如模型子目录前缀）时不算更新：
        # 记录在 data/builtin/.adapted.json，由 local_adapt 维护。
        if local_adapt.is_adapted(rel, remote_blob, _git_blob_sha(local_bytes)):
            continue

        # blob 不同：可能是格式差异，也可能是内容差异；拉单文件确认。
        data = _read_url(RAW_URL.format(name=upstream), timeout=20)
        local_canon = _canonical(local_bytes)
        upstream_canon = _canonical(data)
        if upstream_canon is None:
            changed.append({
                "action": action,
                "file": rel,
                "upstream": upstream,
                "reason": "上游 JSON 解析失败",
                "upstream_bytes": data,
            })
            files[rel] = data
        elif local_canon != upstream_canon:
            changed.append({
                "action": action,
                "file": rel,
                "upstream": upstream,
                "reason": "内容与上游不同",
                "upstream_bytes": data,
            })
            files[rel] = data

    return changed, files, unmapped


def _strip_raw(changed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in item.items() if k != "upstream_bytes"} for item in changed]


def _adapt_all(tree: dict[str, str]) -> list[dict[str, Any]]:
    """对全部内置目标做本机路径适配并记录「已适配」状态。

    ComfyUI 不可达时返回 []（跳过，下次检查再试）；适配只改 data/builtin。
    """
    registry = local_adapt.fetch_registry()
    if registry is None:
        return []
    out: list[dict[str, Any]] = []
    for action, upstream, rel in _targets():
        changes = local_adapt.adapt_file(rel, registry)
        local_adapt.record_adapted(rel, tree.get(f"workflows/{upstream}"))
        if changes:
            out.append({"action": action, "file": rel, "changes": changes})
    return out


def _collect(force_tree: bool = False) -> dict[str, Any]:
    """请求上游并比较；不写 builtin。返回带 _files/_changed_raw 的内部状态。"""
    commit = _latest_commit()
    same_commit = bool(commit["sha"]) and commit["sha"] == _STATE.get("upstream_sha")
    cached_tree = _STATE.get("upstream_blobs") or {}

    if same_commit and cached_tree and not force_tree:
        tree = dict(cached_tree)
    else:
        tree = _git_tree(commit["sha"]) if commit["sha"] else {}
        tree = {
            path: sha for path, sha in tree.items()
            if path.startswith("workflows/") and path.endswith(".api.json")
        }

    changed, files, unmapped = _compare_with_tree(tree)
    state = {
        "checking": False,
        "updating": False,
        "error": None,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "upstream_sha": commit["sha"],
        "upstream_date": commit["date"],
        "upstream_message": commit["message"],
        "upstream_blobs": tree,
        "update_available": bool(changed),
        "changed": _strip_raw(changed),
        "unmapped": unmapped,
        "_files": files,
        "_changed_raw": changed,
    }
    return state


def check_for_update(force: bool = False) -> dict[str, Any]:
    """启动时调用；网络失败只记录 error，不影响服务启动。"""
    with _LOCK:
        _STATE.update({"checking": True, "error": None})
        _save_state()
    try:
        state = _collect(force_tree=force)
        tree = state.get("upstream_blobs") or {}
        state.pop("_files", None)
        state.pop("_changed_raw", None)
        adapted = _adapt_all(tree)
        with _LOCK:
            _STATE.clear()
            _STATE.update(state)
            _STATE.update({
                "checking": False,
                "updating": False,
                "adapted": adapted,
            })
            _save_state()
        return get_status()
    except Exception as e:
        with _LOCK:
            _STATE.update({"checking": False, "updating": False, "error": str(e)})
            _save_state()
        return get_status()


def apply_update() -> dict[str, Any]:
    """一键更新；只写 data/builtin，不碰用户自定义工作流。"""
    with _LOCK:
        if _STATE.get("updating"):
            return {"ok": False, "error": "正在更新中，请稍候"}
        _STATE.update({"updating": True, "error": None})
        _save_state()
    try:
        state = _collect(force_tree=True)
        files = state.pop("_files", {}) or {}
        changed = state.pop("_changed_raw", []) or []
        if not changed:
            with _LOCK:
                _STATE.clear()
                _STATE.update(state)
                _STATE.update({
                    "checking": False,
                    "updating": False,
                    "update_available": False,
                    "changed": [],
                })
                _save_state()
            return {"ok": True, "updated": 0, "actions": [], "message": "已经是最新版本"}

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = BACKUP_DIR / stamp
        backup.mkdir(parents=True, exist_ok=True)
        updated_actions: list[str] = []
        errors: list[str] = []
        for item in changed:
            rel = item["file"]
            action = item["action"]
            if item.get("reason") == "上游 JSON 解析失败":
                errors.append(f"{rel}: 上游 JSON 解析失败，已跳过")
                continue
            data = files.get(rel)
            if data is None:
                errors.append(f"{rel}: 上游文件缺失")
                continue
            local = store.BUILTIN_DIR / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            if local.exists():
                backup.joinpath(rel).parent.mkdir(parents=True, exist_ok=True)
                backup.joinpath(rel).write_bytes(local.read_bytes())
            tmp = local.with_name(local.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, local)
            updated_actions.append(action)

        # 更新写盘后立刻做本机路径适配（模型子目录前缀等），并记录已适配状态，
        # 避免下次检查把适配差异误报成「有更新」。
        adapted = _adapt_all(state.get("upstream_blobs") or {})

        with _LOCK:
            _STATE.clear()
            _STATE.update(state)
            _STATE.update({
                "checking": False,
                "updating": False,
                "update_available": False,
                "changed": [],
                "last_update_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "backup_dir": str(backup),
                "adapted": adapted,
                "error": "; ".join(errors) if errors else None,
            })
            _save_state()
        return {
            "ok": not errors,
            "updated": len(updated_actions),
            "actions": updated_actions,
            "backup_dir": str(backup),
            "adapted": adapted,
            "errors": errors,
        }
    except Exception as e:
        with _LOCK:
            _STATE.update({"updating": False, "error": str(e)})
            _save_state()
        return {"ok": False, "error": str(e)}


def reset_for_tests() -> None:
    """仅测试用：清空内存状态，不碰磁盘。"""
    global _STATE
    with _LOCK:
        _STATE = {
            "checking": False,
            "updating": False,
            "update_available": False,
            "changed": [],
            "unmapped": [],
            "error": None,
            "checked_at": None,
            "upstream_sha": None,
            "upstream_date": None,
            "upstream_message": None,
            "upstream_blobs": {},
            "last_update_at": None,
        }
