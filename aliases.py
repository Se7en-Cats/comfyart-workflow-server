"""模型体检与别名补齐。

动机：工作流常引用「A 量化版本」的文件名，而本机只装了同一模型的「B 量化版本」。
ComfyUI 按文件名精确枚举，名字不在列表就报 "Value not in list"。
本模块：
1. 探测本机模型根目录（ComfyUI Desktop 的 shared_model_paths.yaml / 环境变量）
2. 扫描工作流里的模型引用，找出「缺失但同基名有已装版本」的项
3. 用硬链接创建别名（同盘不占额外空间），让 ComfyUI 同时认识两个名字
"""
import json
import os
import re
from pathlib import Path
from typing import Optional

import store
import models

# 量化后缀：同一模型的不同量化版本，可互为别名。
# 不含 bf16/fp16/fp32（精度差异，保守起见不互相顶替），不含 pruned（剪枝是不同规模）。
QUANT_SUFFIXES = (
    "int8_convrot", "int4_convrot", "nvfp4_awq", "fp8_scaled",
    "fp8_e4m3fn", "w4a8", "w8a8", "int8", "int4",
)

ALIAS_RECORD = store.BASE / "aliases.json"


def model_roots() -> list[Path]:
    """探测模型根目录列表（多个 base_path）。"""
    roots: list[Path] = []
    env = os.environ.get("COMFY_MODEL_PATHS", "")
    for p in env.split(";"):
        p = p.strip()
        if p:
            roots.append(Path(p))
    # ComfyUI Desktop 的共享模型配置
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        yaml = Path(appdata) / "Comfy Desktop" / "shared_model_paths.yaml"
        if yaml.is_file():
            for line in yaml.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r"\s*base_path:\s*['\"]?([^'\"]+)['\"]?", line)
                if m:
                    roots.append(Path(m.group(1)))
    uniq: list[Path] = []
    for r in roots:
        if r.is_dir() and all(str(r) != str(u) for u in uniq):
            uniq.append(r)
    return uniq


def _base_stem(filename: str) -> tuple[str, str]:
    """把文件名拆成 (基名, 量化标签)。如 ..._int8_convrot.safetensors -> (..., 'int8_convrot')。"""
    name = Path(filename).name
    stem = name.rsplit(".", 1)[0]
    low = stem.lower()
    for q in QUANT_SUFFIXES:
        if low == q or low.endswith("_" + q) or low.endswith("-" + q) or low.endswith("." + q):
            cut = len(stem) - len(q)
            while cut > 0 and stem[cut - 1] in "-_. ":
                cut -= 1
            return stem[:cut].lower(), q
    return stem.lower(), ""


def _load_aliases() -> dict:
    try:
        return json.loads(ALIAS_RECORD.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_aliases(data: dict) -> None:
    store.BASE.mkdir(parents=True, exist_ok=True)
    ALIAS_RECORD.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _workflow_files(extra_dirs: list[str]) -> list[Path]:
    """工作流 JSON 来源：data 内置/用户 + 额外目录。"""
    files: list[Path] = []
    for d in (store.BUILTIN_DIR, store.WF_DIR):
        if d.is_dir():
            files += [p for p in d.rglob("*.json") if p.name != ".adapted.json"]
    for d in extra_dirs:
        p = Path(d)
        if p.is_dir():
            files += [
                f for f in p.rglob("*.json")
                if ".bak" not in f.name and ".backup" not in str(f)
            ]
    return files


def _graph_of(path: Path) -> Optional[dict]:
    """从 JSON 读出 API 格式图（只有含 class_type 节点才算）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, dict) and any(
        isinstance(v, dict) and "class_type" in v for v in data.values()
    ):
        return data
    return None


def scan(registry: dict[str, list[str]], extra_dirs: list[str]) -> dict:
    """扫描所有工作流的模型引用，给出缺失项和可补别名建议。"""
    files = _workflow_files(extra_dirs)
    missing_map: dict[tuple[str, str], dict] = {}

    for f in files:
        graph = _graph_of(f)
        if not graph:
            continue
        for ref in models.diff(graph, registry):
            if ref["status"] != "missing":
                continue
            key = (ref["kind"], ref["current"])
            item = missing_map.get(key)
            if item is None:
                base, _quant = _base_stem(ref["current"])
                candidates = [
                    name for name in registry.get(ref["kind"], [])
                    if _base_stem(name)[0] == base
                ]
                item = {
                    "kind": ref["kind"],
                    "current": ref["current"],
                    "candidates": candidates,
                    "action": "alias" if candidates else "download",
                    "download_url": ref.get("download_url", ""),
                    "download_note": ref.get("download_note", ""),
                    "sources": [],
                }
                missing_map[key] = item
            name = str(f)
            if name not in item["sources"]:
                item["sources"].append(name)

    return {
        "scanned": len(files),
        "missing": list(missing_map.values()),
        "roots": [str(r) for r in model_roots()],
    }


_MODEL_EXTS = {".safetensors", ".pt", ".pth", ".ckpt", ".gguf", ".bin", ".sft"}
_INDEX_CACHE: dict[str, dict[str, list[Path]]] = {}


def _file_index(root: Path) -> dict[str, list[Path]]:
    """root 下所有模型文件按 basename(小写) 建索引（进程内缓存）。"""
    key = str(root)
    if key in _INDEX_CACHE:
        return _INDEX_CACHE[key]
    idx: dict[str, list[Path]] = {}
    try:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in _MODEL_EXTS:
                idx.setdefault(p.name.lower(), []).append(p)
    except OSError:
        pass
    _INDEX_CACHE[key] = idx
    return idx


def apply_aliases(items: list[dict]) -> dict:
    """按 items [{kind, alias, target}] 创建硬链接。

    别名建在目标文件所在目录（保证 ComfyUI 枚举能看到这个名字）。
    """
    roots = model_roots()
    created, failed = [], []
    aliases = _load_aliases()

    for it in items:
        kind, alias, target = it.get("kind", ""), it.get("alias", ""), it.get("target", "")
        if not kind or not alias or not target:
            failed.append({**it, "error": "参数不完整"})
            continue
        alias_name = Path(alias).name
        target_rel = Path(target)

        # 1) 优先精确路径 root/kind/target；2) 全 root 索引按文件名找
        real_target: Optional[Path] = None
        for r in roots:
            cand = r / kind / target_rel
            if cand.is_file():
                real_target = cand
                break
        if real_target is None:
            for r in roots:
                hits = _file_index(r).get(target_rel.name.lower(), [])
                if hits:
                    # 多个同名文件时，优先路径尾部与 target 相对路径一致的
                    tail = str(target_rel).replace("/", "\\").lower()
                    real_target = next(
                        (h for h in hits if str(h).lower().endswith(tail)), hits[0]
                    )
                    break
        if real_target is None:
            failed.append({**it, "error": f"找不到目标文件 {target}"})
            continue

        alias_path = real_target.parent / alias_name
        if alias_path.exists():
            created.append({**it, "path": str(alias_path), "note": "已存在，跳过"})
            aliases[f"{kind}/{alias}"] = {"target": f"{kind}/{target}", "path": str(alias_path)}
            continue
        try:
            os.link(real_target, alias_path)   # 硬链接：同盘不占额外空间
            created.append({**it, "path": str(alias_path)})
            aliases[f"{kind}/{alias}"] = {"target": f"{kind}/{target}", "path": str(alias_path)}
            _INDEX_CACHE.pop(str(real_target.parent.parent), None)  # 索引失效
        except OSError as e:
            # 跨盘/无权限时无法建硬链接
            failed.append({**it, "error": f"硬链接失败：{e}"})

    _save_aliases(aliases)
    return {"created": created, "failed": failed}


def state() -> dict:
    """已建别名记录 + 根目录探测结果。"""
    return {
        "roots": [str(r) for r in model_roots()],
        "aliases": _load_aliases(),
    }
