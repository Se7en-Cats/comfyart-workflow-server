"""直连本机 ComfyUI：提交 prompt、查 history、列模型。地址可运行时修改。

ComfyUI Desktop 每次启动可能换端口（8188 / 8189 / 8187 / 8190），所以：
- 配置的地址不通时，自动按候选顺序探测本机（仅当配置的是 127.0.0.1/localhost）；
- 远程/LAN 地址不自动漂移，尊重用户配置。
"""
import os
from urllib.parse import urlparse

import httpx

_default = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
_comfy_url = _default
CLIENT_ID = os.environ.get("COMFY_CLIENT_ID", "mobile-shell")

# 本机 ComfyUI 的常见端口（Desktop 版会漂移）
LOCAL_CANDIDATES = [
    "http://127.0.0.1:8188",
    "http://127.0.0.1:8189",
    "http://127.0.0.1:8187",
    "http://127.0.0.1:8190",
]

_timeout = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=10.0)


def get_comfy_url() -> str:
    return _comfy_url


def set_comfy_url(url: str) -> None:
    global _comfy_url
    _comfy_url = (url or _default).rstrip("/")


async def _reachable(base: str, ms: int = 1200) -> bool:
    try:
        async with httpx.AsyncClient(timeout=ms / 1000) as c:
            r = await c.get(f"{base}/system_stats")
            return r.status_code < 500
    except Exception:
        return False


async def ensure_comfy() -> str:
    """当前地址不通时探测本机候选端口；远程配置不漂移。返回实际使用的地址。"""
    global _comfy_url
    if await _reachable(_comfy_url):
        return _comfy_url
    host = urlparse(_comfy_url).hostname or ""
    if host not in ("127.0.0.1", "localhost"):
        return _comfy_url
    for cand in LOCAL_CANDIDATES:
        if cand == _comfy_url:
            continue
        if await _reachable(cand):
            _comfy_url = cand
            return cand
    return _comfy_url


async def queue_prompt(prompt_graph: dict) -> str:
    await ensure_comfy()
    async with httpx.AsyncClient(timeout=_timeout) as c:
        r = await c.post(f"{_comfy_url}/prompt",
                          json={"prompt": prompt_graph, "client_id": CLIENT_ID})
        if r.status_code >= 400:
            body = r.text
            try:
                data = r.json()
                node_errs = data.get("node_errors") or {}
                msgs = []
                for nid, ne in node_errs.items():
                    for e in ne.get("errors", []):
                        msgs.append(f"#{nid} {ne.get('class_type','')}: {e.get('message','')}")
                if msgs:
                    raise RuntimeError("ComfyUI 节点错误:\n" + "\n".join(msgs))
                if data.get("error"):
                    raise RuntimeError(str(data["error"]))
            except ValueError:
                pass
            raise RuntimeError(f"提交到 ComfyUI 失败 {r.status_code}: {body[:500]}")
        return r.json()["prompt_id"]


async def get_history(prompt_id: str) -> dict | None:
    await ensure_comfy()
    async with httpx.AsyncClient(timeout=_timeout) as c:
        r = await c.get(f"{_comfy_url}/history/{prompt_id}")
        if r.status_code >= 400:
            return None
        data = r.json()
        return data.get(prompt_id)


async def queue() -> dict:
    """ComfyUI 当前队列：{queue_running: [...], queue_pending: [...]}。"""
    await ensure_comfy()
    async with httpx.AsyncClient(timeout=_timeout) as c:
        r = await c.get(f"{_comfy_url}/queue")
        r.raise_for_status()
        return r.json()


async def interrupt() -> None:
    await ensure_comfy()
    async with httpx.AsyncClient(timeout=10.0) as c:
        await c.post(f"{_comfy_url}/interrupt")


async def upload_image(filename: str, content: bytes, subfolder: str = "") -> dict:
    """上传图片到 ComfyUI 的 input 目录，返回 {name, subfolder, type}。"""
    await ensure_comfy()
    async with httpx.AsyncClient(timeout=_timeout) as c:
        r = await c.post(
            f"{_comfy_url}/upload/image",
            files={"image": (filename, content)},
            data={"overwrite": "false", "subfolder": subfolder, "type": "input"},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"ComfyUI 上传失败 {r.status_code}: {r.text[:300]}")
        return r.json()


async def object_info() -> dict:
    """ComfyUI 所有节点类型，用于前端校验节点是否存在。"""
    await ensure_comfy()
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{_comfy_url}/object_info")
        r.raise_for_status()
        return r.json()
