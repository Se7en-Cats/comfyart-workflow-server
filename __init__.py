"""ComfyUI custom-node launcher for ComfyArt Workflow Server.

把这个仓库放进 ComfyUI 的 `custom_nodes/` 后：
- ComfyUI 启动时自动在本目录拉起服务（默认端口 8811）；
- ComfyUI 退出后服务随之退出；
- 服务已经在跑（手动启动过）时直接复用，不重复启动；
- 缺依赖时自动用 ComfyUI 的 Python 环境安装（后台进行，不阻塞 ComfyUI 启动）。

也可以完全不放进 custom_nodes，直接 `python start-server.py` 独立运行。
"""

import os
import subprocess
import sys
import threading
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8811"))
HEALTH_URL = f"http://127.0.0.1:{PORT}/health"
REQUIREMENTS = os.path.join(BASE, "requirements.txt")

# 声明前端扩展目录（ComfyUI 据此把 web/**/*.js 挂载到 /extensions）
WEB_DIRECTORY = "./web"

_DEPS = ("fastapi", "uvicorn", "httpx", "pydantic", "multipart")


def _service_alive() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _missing_deps() -> list:
    missing = []
    for mod in _DEPS:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return missing


def _comfy_port_from_argv():
    """从 ComfyUI 启动命令行解析 --port（服务应连拉起它的这个实例）。"""
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--port="):
            return a.split("=", 1)[1]
    return None


def _start_server(parent_pid: int) -> None:
    """用当前 Python（ComfyUI 的环境）拉起服务子进程。"""
    try:
        env = dict(os.environ)
        port = _comfy_port_from_argv()
        if port and port.isdigit():
            env.setdefault("COMFY_PORT", port)
        subprocess.Popen(
            [sys.executable, os.path.join(BASE, "start-server.py"), "--parent", str(parent_pid)],
            cwd=BASE,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass  # 启动失败不拖累 ComfyUI 启动


def _install_deps_and_start(parent_pid: int) -> None:
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", REQUIREMENTS],
            cwd=BASE,
            capture_output=True,
        )
    except Exception:
        pass
    _start_server(parent_pid)


def _maybe_launch() -> None:
    if _service_alive():
        print(
            f"[ComfyArt Workflow Server] 已在运行，复用现有实例："
            f"http://127.0.0.1:{PORT}/"
        )
        return
    missing = _missing_deps()
    parent_pid = os.getpid()
    if missing:
        print(
            f"[ComfyArt Workflow Server] 首次使用，正在用 ComfyUI 的 Python "
            f"安装依赖：{', '.join(missing)}（后台进行）"
        )
        threading.Thread(
            target=_install_deps_and_start, args=(parent_pid,), daemon=True
        ).start()
    else:
        _start_server(parent_pid)
        print(
            f"[ComfyArt Workflow Server] 已随 ComfyUI 启动："
            f"http://127.0.0.1:{PORT}/ （别名 /ArtWorkflow）"
        )


# ComfyUI 在 import 本包时即触发；独立运行（python start-server.py）不会走到这里。
if os.environ.get("COMFYART_NO_AUTOSTART") != "1":
    _maybe_launch()
