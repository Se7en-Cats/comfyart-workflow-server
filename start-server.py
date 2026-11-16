"""Start the ComfyArt workflow backend detached, with file logging.

Used by the Windows scheduled task `ComfyArtWorkflowServer`.
"""

import os
import sys
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "data", "server.log")


def main() -> None:
    os.chdir(BASE)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    sys.stdout = open(LOG, "a", encoding="utf-8", buffering=1)
    sys.stderr = sys.stdout
    print("\n=== starting ComfyArt Workflow Server ===")
    try:
        import uvicorn
        uvicorn.run("main:app", host="0.0.0.0", port=8811, log_level="info")
    except Exception:
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
