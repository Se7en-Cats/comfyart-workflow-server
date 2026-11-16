"""扫描所有内置/已上传工作流用到的节点，对比本地 ComfyUI 已注册节点，找出缺失的第三方自定义节点。"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).parent
DATA = ROOT / "data"

# ComfyUI 核心自带节点（不依赖第三方插件）
CORE_NODES = {
    "KSampler", "KSamplerAdvanced", "CheckpointLoaderSimple", "LoraLoader",
    "CLIPTextEncode", "CLIPTextEncodeSDXL", "EmptyLatentImage", "EmptySD3LatentImage",
    "LoadImage", "LoadImageMask", "VAEDecode", "VAEEncode", "VAELoader", "VAEDecodeTiled",
    "SaveImage", "PreviewImage", "ImageScale", "ImageScaleBy", "ImageScaleToTotalPixels",
    "ModelLoaderSimple", "UNETLoader", "DualCLIPLoader", "CLIPLoader", "ControlNetLoader",
    "ControlNetApply", "ControlNetApplyAdvanced", "ApplyControlNet", " conditioningConcat",
    "ConditioningCombine", "ConditioningConcat", "ConditioningAverage", "ConditioningSetArea",
    "ConditioningSetAreaPercentage", "ConditioningSetMask", "ConditioningZeroOut",
    "LatentUpscale", "LatentUpscaleBy", "LatentFromBatch", "LatentBatch", "LatentComposite",
    "RepeatLatentBatch", "RepeatImageBatch", "ImageBatch", "ImageCrop", "ImageFlip",
    "ImagePadForOutpaint", "MaskToImage", "ImageToMask", "SolidMask", "FeatherMask",
    "GrowMask", "InvertMask", "MaskComposite", "MaskAdd", "MaskSubtract", "MaskMultiply",
    "Note", "Reroute", "PrimitiveNode", "String", "Float", "Integer", "CheckpointLoader",
    "VAEEncodeInpaint", "SetUnionMask", "GetUnionMask", "MaskFill", "CropImage",
    "ImageResize", "ImageOutpaint", "ColorCorrect", "Blur", "MaskBlur", "ImageColorToMask",
    "DontImport", "ImageScaleDown", "ModelSamplingDiscrete", "RescaleCFG", "GuiderCombine",
    "DualCLIPLoaderGGUF", "UnETLoader", "CLIPLoaderGGUF", "UnetLoaderGGUF",
}

def load_graph(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "nodes" in raw and "links" in raw:  # UI format
        return {}
    return raw

def collect_nodes() -> dict[str, list[str]]:
    """节点类型 -> 用到它的工作流文件列表。"""
    node_users = defaultdict(list)
    files = list(DATA.glob("builtin/*.json")) + list(DATA.glob("workflows/**/*.json"))
    for f in files:
        try:
            graph = load_graph(f)
        except Exception as e:
            print(f"[跳过] {f.name}: {e}")
            continue
        if not graph:
            continue
        for nid, node in graph.items():
            ct = node.get("class_type") if isinstance(node, dict) else None
            if ct:
                node_users[ct].append(f"{f.parent.name}/{f.name}")
    return node_users

def main():
    cfg = json.loads((DATA / "config.json").read_text(encoding="utf-8")) if (DATA / "config.json").exists() else {}
    url = cfg.get("comfy_url", "http://127.0.0.1:8189").rstrip("/")
    print(f"ComfyUI: {url}\n")

    node_users = collect_nodes()
    print(f"共扫描到 {len(node_users)} 种不同节点\n")

    try:
        r = httpx.get(f"{url}/object_info", timeout=15.0)
        r.raise_for_status()
        installed = set(r.json().keys())
        print(f"本地 ComfyUI 已注册节点: {len(installed)} 个\n")
    except Exception as e:
        print(f"无法连接 ComfyUI ({e})，只能按核心节点名单判断第三方依赖。\n")
        installed = None

    missing, thirdparty_ok, core = [], [], []
    for ct, users in sorted(node_users.items()):
        installed_flag = installed is None or ct in installed
        if ct in CORE_NODES:
            core.append((ct, users, installed_flag))
        else:
            if installed_flag:
                thirdparty_ok.append((ct, users))
            else:
                missing.append((ct, users))

    print("=" * 60)
    if missing:
        print(f"⚠ 缺失节点（本地 ComfyUI 未注册，运行会报红）: {len(missing)}")
        for ct, users in missing:
            print(f"  ✗ {ct}")
            for u in sorted(set(users))[:3]:
                print(f"      └ {u}")
    else:
        print("✓ 没有缺失节点，所有用到的第三方节点本地都已安装")

    print("=" * 60)
    print(f"已安装的第三方自定义节点: {len(thirdparty_ok)}")
    for ct, users in thirdparty_ok:
        print(f"  ✓ {ct}  ({len(set(users))} 个工作流)")

    print("=" * 60)
    print(f"ComfyUI 核心自带节点: {len(core)}")

if __name__ == "__main__":
    main()
