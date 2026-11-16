"""ComfyArt 风格工作流管理后端（FastAPI）。

启动：
  pip install -r requirements.txt
  uvicorn main:app --host 0.0.0.0 --port 8811

环境变量：
  COMFY_URL        ComfyUI 地址，默认 http://127.0.0.1:8188
  COMFYART_WF_DATA 数据目录，默认 ./data
"""
import asyncio
import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import comfy
import store
import models
import autotag
import art_updater
from actions import ACTIONS, GROUPS
from markers import (as_api_workflow, check_workflow, fill_workflow,
                     find_markers)

# 启动时从 data/config.json 读 ComfyUI 地址（环境变量优先）
_saved = store.get_app_config()
if os.environ.get("COMFY_URL"):
    comfy.set_comfy_url(os.environ["COMFY_URL"])
elif _saved.get("comfy_url"):
    comfy.set_comfy_url(_saved["comfy_url"])

app = FastAPI(title="ComfyArt Workflow Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup_art_update_check():
    # 启动后后台检查 ComfyArt 上游；不阻塞服务启动。
    asyncio.create_task(asyncio.to_thread(art_updater.check_for_update))


# ---------- 模型 ----------
class SaveWorkflowBody(BaseModel):
    name: str
    workflow: dict[str, Any]
    autotag: bool = True  # 保存前自动补 @marker（ComfyUI API 导出通常没有标记）


class CheckBody(BaseModel):
    workflow: dict[str, Any]


class AutoTagBody(BaseModel):
    workflow: dict[str, Any]


class BindingBody(BaseModel):
    filename: Optional[str] = None  # null = 回退内置


class VariantBody(BaseModel):
    variant: Optional[str] = None  # null = 用默认内置 <action>.json


class ConfigBody(BaseModel):
    comfy_url: Optional[str] = None


class LoraItem(BaseModel):
    name: str
    strength: float = 1.0


class RunBody(BaseModel):
    image: Optional[str] = None
    image2: Optional[str] = None
    image3: Optional[str] = None
    base: Optional[str] = None
    front: Optional[str] = None
    left: Optional[str] = None
    back: Optional[str] = None
    right: Optional[str] = None
    mask: Optional[str] = None
    last: Optional[str] = None
    video: Optional[str] = None
    audio: Optional[str] = None
    prompt: Optional[str] = None
    negative: Optional[str] = None
    lyrics: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    count: Optional[int] = None
    seconds: Optional[float] = None
    strength: Optional[float] = None
    loras: list[LoraItem] = []


class Replacement(BaseModel):
    node_id: str
    field: str
    value: str


class ReplaceBody(BaseModel):
    replacements: list[Replacement]


# ---------- 基础 ----------
def _require_action(action: str):
    if action not in ACTIONS:
        raise HTTPException(404, f"未知槽位 {action}，可选: {list(ACTIONS)}")
    return ACTIONS[action]


def _bound_graph(action: str) -> dict | None:
    """当前槽位实际生效的工作流：绑定 > 内置变体 > 内置默认。"""
    try:
        graph, _source, _fn = store.resolve_workflow(action)
        return graph
    except RuntimeError:
        return None


@app.get("/health")
async def health():
    try:
        await comfy.object_info()
        comfy_ok = True
    except Exception as e:
        comfy_ok = False
        detail = str(e)
    return {
        "ok": comfy_ok,
        "comfy_url": comfy.get_comfy_url(),
        **({} if comfy_ok else {"error": detail}),
    }


@app.get("/config")
async def get_config():
    return {"comfy_url": comfy.get_comfy_url()}


@app.post("/config")
async def post_config(body: ConfigBody):
    if body.comfy_url:
        url = body.comfy_url.strip().rstrip("/")
        if not url.startswith("http"):
            url = "http://" + url
        comfy.set_comfy_url(url)
        cfg = store.get_app_config()
        cfg["comfy_url"] = url
        store.set_app_config(cfg)
    return {"ok": True, "comfy_url": comfy.get_comfy_url()}


@app.get("/art/status")
async def art_status():
    """ComfyArt 内置工作流的上游检查状态。"""
    return art_updater.get_status()


@app.post("/art/check")
async def art_check():
    """手动重新检查上游（启动时也会自动检查一次）。"""
    return await asyncio.to_thread(art_updater.check_for_update, True)


@app.post("/art/update")
async def art_update():
    """一键把有更新的内置工作流同步到 data/builtin。"""
    result = await asyncio.to_thread(art_updater.apply_update)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "更新失败")
    return result


@app.get("/actions")
async def list_actions():
    """所有槽位 + 元数据 + 当前绑定。"""
    out = []
    for spec in ACTIONS.values():
        binding = store.get_binding(spec.id)
        graph = _bound_graph(spec.id)
        out.append({
            "id": spec.id,
            "group": spec.group,
            "group_label": GROUPS[spec.group],
            "title_cn": spec.title_cn,
            "position": spec.position,
            "required": [f"@{m}" for m in spec.required],
            "optional": [f"@{m}" for m in spec.optional],
            "needs_images": spec.needs_images,
            "needs_frames": spec.needs_frames,
            "note": spec.note,
            "bound": binding,
            "bound_markers": find_markers(graph) if graph else {},
            "builtin_available": store.read_builtin(spec.id) is not None,
            "builtin_variants": store.list_builtin_variants(spec.id),
            "builtin_variant": store.get_variant(spec.id),
            "files": store.list_workflows(spec.id),
        })
    return {"actions": out}


# ---------- 工作流 CRUD ----------
@app.get("/workflows/{action}")
async def list_wf(action: str):
    _require_action(action)
    return {"action": action, "files": store.list_workflows(action),
            "bound": store.get_binding(action),
            "builtin_variants": store.list_builtin_variants(action),
            "builtin_variant": store.get_variant(action)}


@app.post("/workflows/{action}/check")
async def check_wf(action: str, body: CheckBody):
    _require_action(action)
    try:
        graph = as_api_workflow(body.workflow)
    except ValueError as e:
        raise HTTPException(400, str(e))
    result = check_workflow(graph, action)
    result["ok"] = not result["errors"]
    return result


@app.post("/workflows/{action}/autotag")
async def autotag_preview(action: str, body: AutoTagBody):
    """只推断不落盘：返回建议标记 + 打好标记的工作流副本。"""
    _require_action(action)
    try:
        graph = as_api_workflow(body.workflow)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return autotag.suggest(graph, action)


@app.post("/workflows/{action}/{filename}/autotag")
async def autotag_file(action: str, filename: str):
    """给已上传的工作流补标记（原文件先备份到 <action>/.backup/）。"""
    _require_action(action)
    graph = store.read_workflow(action, filename)
    if graph is None:
        raise HTTPException(404, f"{filename} 不存在于槽位 {action}")
    result = autotag.suggest(graph, action)
    if result["changes"]:
        backup = store.WF_DIR / action / ".backup"
        backup.mkdir(parents=True, exist_ok=True)
        (backup / filename).write_text(
            json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
        store.write_workflow(action, filename, result["workflow"])
    return {
        "ok": True,
        "file": filename,
        "changes": result["changes"],
        "unresolved": result["unresolved"],
        "errors": result["errors"],
        "warnings": result["warnings"],
    }


@app.post("/workflows/{action}")
async def save_wf(action: str, body: SaveWorkflowBody):
    _require_action(action)
    try:
        graph = as_api_workflow(body.workflow)
    except ValueError as e:
        raise HTTPException(400, str(e))
    auto = autotag.suggest(graph, action) if body.autotag else None
    if auto:
        graph = auto["workflow"]
    checked = check_workflow(graph, action)
    if checked["errors"]:
        raise HTTPException(422, {
            "message": "校验未通过，未保存",
            "errors": checked["errors"],
            "warnings": checked["warnings"],
            "markers": checked["markers"],
            "autotag": auto["changes"] if auto else [],
            "unresolved": auto["unresolved"] if auto else [],
        })
    filename = store.save_workflow(action, body.name, graph)
    # 保存后自动绑定到该槽位（ComfyArt 也是这个行为）
    store.set_binding(action, filename)
    return {"ok": True, "file": filename, "bound": action,
            "warnings": checked["warnings"], "markers": checked["markers"],
            "autotag": auto["changes"] if auto else [],
            "unresolved": auto["unresolved"] if auto else []}


@app.delete("/workflows/{action}/{filename}")
async def delete_wf(action: str, filename: str):
    _require_action(action)
    removed = store.delete_workflow(action, filename)
    if store.get_binding(action) == filename:
        store.set_binding(action, None)
    return {"ok": removed, "bound": store.get_binding(action)}


# ---------- 绑定 ----------
@app.put("/bindings/{action}")
async def set_binding(action: str, body: BindingBody):
    _require_action(action)
    if body.filename:
        if store.read_workflow(action, body.filename) is None:
            raise HTTPException(404, f"{body.filename} 不存在于槽位 {action}")
        # 绑定前校验一次
        graph = store.read_workflow(action, body.filename)
        checked = check_workflow(graph, action)  # type: ignore[arg-type]
        if checked["errors"]:
            raise HTTPException(422, {"errors": checked["errors"]})
    store.set_binding(action, body.filename)
    return {"action": action, "bound": store.get_binding(action)}


@app.put("/bindings/{action}/variant")
async def set_builtin_variant(action: str, body: VariantBody):
    """选择内置变体（只有未绑定自定义工作流时生效）。"""
    _require_action(action)
    if body.variant:
        if body.variant not in store.list_builtin_variants(action):
            raise HTTPException(404, f"变体 {body.variant} 不存在于槽位 {action}")
    store.set_variant(action, body.variant)
    return {"action": action, "builtin_variant": store.get_variant(action)}


# ---------- 文件上传（图片/视频，转发到 ComfyUI 的 input 目录） ----------
@app.post("/uploads")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")
    suffix = os.path.splitext(file.filename or "")[1].lower()
    is_video = (file.content_type or "").startswith("video/") \
        or suffix in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    limit_mb = 512 if is_video else 64
    if len(content) > limit_mb * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {limit_mb}MB")
    try:
        res = await comfy.upload_image(file.filename or "image.png", content)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    name = res.get("name") or file.filename or "image.png"
    sub = (res.get("subfolder") or "").strip("/")
    return {"ok": True, "filename": f"{sub}/{name}" if sub else name,
            "type": res.get("type", "input")}


# ---------- 执行 ----------
@app.post("/run/{action}")
async def run(action: str, body: RunBody):
    _require_action(action)
    try:
        graph, source, filename = store.resolve_workflow(action)
    except RuntimeError as e:
        raise HTTPException(409, str(e))

    markers = find_markers(graph)
    checked = check_workflow(graph, action)
    if checked["errors"]:
        raise HTTPException(422, {"message": "当前绑定的工作流校验失败",
                                  "errors": checked["errors"]})

    values = {k: v for k, v in body.model_dump().items() if v is not None}

    # 传了图/音频但工作流没有对应标记时，不静默忽略：ComfyArt 原版会直接报错，
    # 这里给出可操作的提示（常见于旧文件还没补 @image2/@image3）。
    unmarked = [f"@{k}" for k in
                ("image", "image2", "image3", "base", "front", "left", "back",
                 "right", "mask", "last", "video", "audio", "lyrics", "negative")
                if values.get(k) and k not in markers]
    if unmarked:
        raise HTTPException(422, {
            "message": f"工作流没有 {', '.join(unmarked)} 标记，填了也不会生效",
            "hint": "先点左侧「自动补标记」，或在 ComfyUI 里给对应节点标题加上该标记后重新导出",
            "markers": markers,
            "unmarked": unmarked,
        })

    if values.get("loras") and "unet" not in markers:
        raise HTTPException(422, {
            "message": "工作流没有 @unet 标记，LoRA 不会生效",
            "hint": "在 ComfyUI 里给模型加载节点（UNETLoader / CheckpointLoader）标题加 @unet，或点左侧「自动补标记」",
            "markers": markers,
        })

    output_node = fill_workflow(graph, markers, values)

    try:
        prompt_id = await comfy.queue_prompt(graph)
    except RuntimeError as e:
        msg = str(e)
        if "LoadImage" in msg and "validation failed" in msg:
            named = [f"@{m}" for m, nid in markers.items() if f"#{nid}" in msg]
            if named:
                msg += "\n涉及输入：" + "、".join(named)
            msg += ("\n提示：LoadImage 找不到这张图（文件名不在 ComfyUI 的 input 目录）。"
                    "请点输入框右侧「上传」把本地图片传上去，或改填 input 目录里已存在的文件名。")
        raise HTTPException(502, msg)

    return {"prompt_id": prompt_id, "action": action, "source": source,
            "file": filename, "output_node": output_node}


@app.get("/jobs/{prompt_id}")
async def job_status(prompt_id: str):
    hist = await comfy.get_history(prompt_id)
    if hist is None:
        # history 只有在任务结束后才有；运行中/排队中要查 /queue，否则前端会一直显示排队
        try:
            q = await comfy.queue()
            running = [item[1] for item in q.get("queue_running") or []]
            pending = [item[1] for item in q.get("queue_pending") or []]
            if prompt_id in running:
                return {"prompt_id": prompt_id, "status": "running",
                        "queue_position": 0}
            if prompt_id in pending:
                return {"prompt_id": prompt_id, "status": "queuing",
                        "queue_position": pending.index(prompt_id) + 1}
        except Exception:
            pass
        return {"prompt_id": prompt_id, "status": "queuing"}

    status = hist.get("status") or {}
    messages = status.get("messages") or []
    if any(m[0] == "execution_error" for m in messages):
        err = next((m for m in messages if m[0] == "execution_error"), None)
        raise HTTPException(500, {"status": "error",
                                   "detail": (err[1] if err else {}).get("exception_message")})
    if status.get("completed") or status.get("status_str") == "success":
        outputs = hist.get("outputs") or {}
        files = []
        for node_id, out in outputs.items():
            for kind in ("images", "videos", "gifs", "audio", "3d"):
                for item in out.get(kind, []) or []:
                    files.append({
                        "node": node_id,
                        "kind": kind.rstrip("s"),
                        "filename": item.get("filename"),
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                        "url": f"{comfy.get_comfy_url()}/view?filename="
                               f"{item.get('filename')}&subfolder="
                               f"{item.get('subfolder','')}&type={item.get('type','output')}",
                    })
            if out.get("text"):
                files.append({"node": node_id, "kind": "text",
                              "text": "".join(out["text"])})
        return {"prompt_id": prompt_id, "status": "success", "files": files}

    if any(m[0] == "execution_interrupted" for m in messages):
        raise HTTPException(499, {"status": "interrupted"})
    return {"prompt_id": prompt_id, "status": "running"}


@app.post("/interrupt")
async def interrupt():
    await comfy.interrupt()
    return {"ok": True}


@app.get("/loras")
async def list_loras():
    """列出本地 ComfyUI 已装的 LoRA 文件，供 Advanced 面板选择。"""
    try:
        info = await _cached_object_info()
    except Exception as e:
        raise HTTPException(502, f"ComfyUI 不可达: {e}")
    for cls in ("LoraLoader", "LoraLoaderModelOnly", "Power Lora Loader (rgthree)"):
        node = info.get(cls)
        if not node:
            continue
        req = (node.get("input") or {}).get("required") or {}
        for key in ("lora_name", "lora_names"):
            spec = req.get(key)
            if isinstance(spec, list) and spec and isinstance(spec[0], list):
                return {"loras": spec[0]}
    return {"loras": []}


# object_info 全量节点清单较大，模型/LoRA 清单短时间内不变，做 TTL 缓存。
# 仅缓存成功结果；ComfyUI 不可达时不缓存，下次重试。
_info_cache: dict = {"ts": 0.0, "data": None}
_INFO_TTL = 60.0


async def _cached_object_info() -> dict:
    now = time.time()
    if _info_cache["data"] is not None and now - _info_cache["ts"] < _INFO_TTL:
        return _info_cache["data"]
    info = await comfy.object_info()
    _info_cache["ts"] = now
    _info_cache["data"] = info
    return info


async def _model_registry() -> dict[str, list[str]]:
    try:
        info = await _cached_object_info()
        return models.build_registry(info)
    except Exception as e:
        raise HTTPException(502, f"ComfyUI 不可达: {e}")


def _resolve_editable(action: str, filename: Optional[str]):
    """返回 (graph, source, filename)。builtin 只读，编辑时由调用方复制。"""
    if filename:
        g = store.read_workflow(action, filename)
        if g is None:
            raise HTTPException(404, f"{filename} 不存在")
        return g, "custom", filename
    g, source, fn = store.resolve_workflow(action)
    return g, source, fn


@app.get("/models")
async def model_registry():
    return await _model_registry()


@app.get("/workflows/{action}/models")
async def workflow_models(action: str, file: Optional[str] = None):
    _require_action(action)
    graph, source, fn = _resolve_editable(action, file)
    registry = await _model_registry()
    deps = models.diff(graph, registry)
    return {"action": action, "source": source, "file": fn, "dependencies": deps}


@app.put("/workflows/{action}/models")
async def replace_models(action: str, body: ReplaceBody, file: Optional[str] = None):
    _require_action(action)
    graph, source, fn = _resolve_editable(action, file)
    original = copy.deepcopy(graph)
    changed = models.apply_replacements(graph, [r.model_dump() for r in body.replacements])
    if changed:
        # 保存前用 ComfyUI 实时枚举二次校验：候选文件可能已被删除/移动，
        # 写进工作流会造成运行时 "Value not in list" 报错。
        registry = await _model_registry()
        kind_of = {r["node_id"]: r["kind"] for r in models.scan_models(graph)}
        bad = [
            f"#{c['node_id']} {c['field']}={c['to']}（{kind_of.get(c['node_id'], '?')} 列表里没有）"
            for c in changed
            if kind_of.get(c["node_id"]) in registry
            and c["to"] not in registry[kind_of[c["node_id"]]]
        ]
        if bad:
            raise HTTPException(
                422,
                "保存被拦截，以下模型文件在当前 ComfyUI 里不存在（可能已删除、移动或未下载完）："
                + "；".join(bad),
            )
    # 内置工作流只读：复制成一个自定义文件再改
    if source == "builtin":
        graph_copy = copy.deepcopy(graph)
        fn = f"{action}-custom.json"
        store.write_workflow(action, fn, graph_copy)
        store.set_binding(action, fn)
    if changed:
        backup = store.WF_DIR / action / ".backup"
        backup.mkdir(parents=True, exist_ok=True)
        (backup / fn).write_text(
            json.dumps(original, indent=2, ensure_ascii=False), encoding="utf-8")
    store.write_workflow(action, fn, graph)
    return {"ok": True, "file": fn, "changed": changed}


# 入口别名：http://127.0.0.1:8811/ArtWorkflow 直达管理界面，
# 地址栏保持 /ArtWorkflow（前端单文件、API 全绝对路径，不受影响）。
# 必须注册在 mount("/") 之前，否则会被兜底 mount 吞掉。
_WEB_DIR = Path(__file__).parent / "web"


@app.get("/ArtWorkflow", include_in_schema=False)
@app.get("/ArtWorkflow/", include_in_schema=False)
async def _artworkflow_page():
    from fastapi.responses import FileResponse
    return FileResponse(_WEB_DIR / "index.html")


# 管理界面（必须挂在所有 API 路由之后，作为兜底）
if _WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8811")))
