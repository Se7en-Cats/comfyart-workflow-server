"""工作流文件与绑定配置的本地存储。

目录布局：
  data/
    workflows/<action>/<name>.json   # 用户上传/编辑的工作流（API format）
    builtin/<action>.json            # 内置兜底工作流（用户可从 ComfyArt 仓库 workflows/ 拷入）
    workflow-config.json              # {action: filename}，null/缺省=回退内置
"""
import json
import os
import re
from pathlib import Path

BASE = Path(os.environ.get("COMFYART_WF_DATA", "./data")).resolve()
WF_DIR = BASE / "workflows"
BUILTIN_DIR = BASE / "builtin"
CONFIG = BASE / "workflow-config.json"
APP_CONFIG = BASE / "config.json"

_SAFE = re.compile(r'[\\/:*?"<>|]')


def _ensure() -> None:
    WF_DIR.mkdir(parents=True, exist_ok=True)
    BUILTIN_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists():
        CONFIG.write_text("{}", encoding="utf-8")
    if not APP_CONFIG.exists():
        APP_CONFIG.write_text("{}", encoding="utf-8")


def get_app_config() -> dict:
    _ensure()
    try:
        return json.loads(APP_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_app_config(data: dict) -> None:
    _ensure()
    APP_CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                          encoding="utf-8")


def list_workflows(action: str) -> list[str]:
    _ensure()
    d = WF_DIR / action
    if not d.exists():
        return []
    return sorted(p.name for p in d.glob("*.json"))


def save_workflow(action: str, name: str, graph: dict) -> str:
    _ensure()
    d = WF_DIR / action
    d.mkdir(parents=True, exist_ok=True)
    safe = _SAFE.sub("_", name)
    if not safe.endswith(".json"):
        safe += ".json"
    (d / safe).write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    return safe


def read_workflow(action: str, filename: str) -> dict | None:
    p = WF_DIR / action / filename
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def write_workflow(action: str, filename: str, graph: dict) -> None:
    _ensure()
    d = WF_DIR / action
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(
        json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")


def delete_workflow(action: str, filename: str) -> bool:
    p = WF_DIR / action / filename
    if p.exists():
        p.unlink()
        return True
    return False


def read_builtin(action: str) -> dict | None:
    p = BUILTIN_DIR / f"{action}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_builtin_variants(action: str) -> list[str]:
    """data/builtin/<action>/ 下的内置变体（如 generate 的多种底模工作流）。"""
    d = BUILTIN_DIR / action
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("*.json"))


def read_builtin_variant(action: str, name: str) -> dict | None:
    # 只接受纯文件名，防止路径穿越
    if name != os.path.basename(name):
        return None
    p = BUILTIN_DIR / action / name
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _read_config() -> dict:
    _ensure()
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def get_binding(action: str) -> str | None:
    return _read_config().get(action)


def set_binding(action: str, filename: str | None) -> None:
    cfg = _read_config()
    if filename:
        cfg[action] = filename
    else:
        cfg.pop(action, None)
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def get_variant(action: str) -> str | None:
    """内置变体选择（workflow-config.json 的 variants 字段）。"""
    variants = _read_config().get("variants")
    if not isinstance(variants, dict):
        return None
    return variants.get(action)


def set_variant(action: str, name: str | None) -> None:
    cfg = _read_config()
    variants = cfg.get("variants")
    if not isinstance(variants, dict):
        variants = {}
    if name:
        variants[action] = name
    else:
        variants.pop(action, None)
    cfg["variants"] = variants
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve_workflow(action: str) -> tuple[dict, str, str]:
    """返回 (graph, 来源, 文件名)。来源: 'custom' | 'builtin'。

    优先级：自定义绑定 > 内置变体（variants 选择）> 内置默认 <action>.json。
    """
    fn = get_binding(action)
    if fn:
        g = read_workflow(action, fn)
        if g is not None:
            return g, "custom", fn
    variant = get_variant(action)
    if variant:
        g = read_builtin_variant(action, variant)
        if g is not None:
            return g, "builtin", f"{action}/{variant}"
    g = read_builtin(action)
    if g is not None:
        return g, "builtin", f"{action}.json"
    raise RuntimeError(f"槽位 {action} 没有可用工作流：请先上传或在 builtin/ 放入 {action}.json")
