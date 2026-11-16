"""Start the ComfyArt workflow backend detached, with file logging.

Used by:
- the Windows scheduled task `ComfyArtWorkflowServer`;
- the ComfyUI custom-node launcher (`__init__.py`), which passes `--parent <pid>`
  so this process exits together with ComfyUI.
"""

import argparse
import ctypes
import os
import sys
import threading
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "data", "server.log")
PORT = int(os.environ.get("PORT", "8811"))


def _parent_alive_windows(pid: int) -> bool:
    """OpenProcess 探测父进程是否存活（无需 psutil）。"""
    try:
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


def _parent_alive_posix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _watch_parent(pid: int) -> None:
    alive = _parent_alive_windows if os.name == "nt" else _parent_alive_posix

    def loop() -> None:
        while True:
            threading.Event().wait(3)
            if not alive(pid):
                print(f"[watcher] parent {pid} exited, shutting down")
                os._exit(0)

    threading.Thread(target=loop, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=int, default=None,
                        help="父进程 PID；父进程退出时本进程跟随退出")
    args = parser.parse_args()

    os.chdir(BASE)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    sys.stdout = open(LOG, "a", encoding="utf-8", buffering=1)
    sys.stderr = sys.stdout
    # 由 ComfyUI 插件拉起时，固定连「拉起本服务的那个 ComfyUI 实例」；
    # 用户显式设了 COMFY_URL 时不覆盖。
    _comfy_port = os.environ.get("COMFY_PORT")
    if _comfy_port and _comfy_port.isdigit():
        os.environ.setdefault("COMFY_URL", f"http://127.0.0.1:{_comfy_port}")
        print(f"[launcher] ComfyUI 端口 {_comfy_port} -> 固定连接该实例")
    print("\n=== starting ComfyArt Workflow Server ===")
    if args.parent:
        _watch_parent(args.parent)
        print(f"[watcher] following parent pid {args.parent}")
    try:
        import uvicorn
        uvicorn.run("main:app", host="0.0.0.0", port=PORT, log_level="info")
    except Exception:
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
