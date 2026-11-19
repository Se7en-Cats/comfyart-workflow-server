"""模型依赖扫描与本地清单对比。

ComfyUI API format 工作流里，模型加载节点通过 widget 字段引用模型文件名。
这里：
1. 从 /object_info 提取本地已装模型（按 checkpoint / diffusion / clip / vae / lora 分组）
2. 扫描工作流里所有模型引用，标出缺失 / 可用
3. 提供按节点+字段替换模型名的函数，写回 JSON
"""
from typing import Any, Optional
import os

import autofill

# class_type -> [(field, kind), ...]
LOADERS: dict[str, list[tuple[str, str]]] = {
    "CheckpointLoaderSimple": [("ckpt_name", "checkpoints")],
    "CheckpointLoader": [("ckpt_name", "checkpoints")],
    "CheckpointLoaderGGUF": [("ckpt_name", "checkpoints")],
    "CheckpointLoaderKJ": [("ckpt_name", "checkpoints")],
    "UNETLoader": [("unet_name", "diffusion_models")],
    "UnETLoaderGGUF": [("unet_name", "diffusion_models")],
    "LoadDiffusionModel": [("unet_name", "diffusion_models")],
    "ComfyArtPreciseUNETLoader": [("unet_name", "diffusion_models")],
    "DiffusionModelLoaderKJ": [("model_name", "diffusion_models")],
    "UNETLoader_Any": [("unet_name", "diffusion_models")],
    "CLIPLoader": [("clip_name", "clip")],
    "CLIPLoader_Any": [("clip_name", "clip")],
    "DualCLIPLoader": [("clip_name1", "clip"), ("clip_name2", "clip")],
    "DualCLIPLoader_Any": [("clip_name1", "clip"), ("clip_name2", "clip")],
    "DualCLIPLoaderGGUF": [("clip_name1", "clip"), ("clip_name2", "clip")],
    "CLIPVisionLoader": [("clip_name", "clip_vision")],
    "VAELoader": [("vae_name", "vae")],
    "VAELoaderKJ": [("vae_name", "vae")],
    "LoraLoader": [("lora_name", "loras")],
    "LoraLoaderModelOnly": [("lora_name", "loras")],
    "LoraLoader_Any": [("lora_name", "loras")],
    "LoraLoaderModelOnly_Any": [("lora_name", "loras")],
    "Lora Loader Stack (rgthree)": [
        ("lora_01", "loras"), ("lora_02", "loras"),
        ("lora_03", "loras"), ("lora_04", "loras"),
    ],
    "DownloadAndLoadGIMMVFIModel": [("model", "interpolation")],
    "ControlNetLoader": [("control_net_name", "controlnet")],
    "DiffControlNetLoader": [("control_net_name", "controlnet")],
    "StyleModelLoader": [("style_model_name", "style_models")],
    "UpscaleModelLoader": [("model_name", "upscale_models")],
    # mockup 贴图工作流（mockup-surface）：深度预处理 ckpt + 抠图模型
    "DepthAnythingV2Preprocessor": [("ckpt_name", "depth_preprocessors")],
    "LoadBackgroundRemovalModel": [("bg_removal_name", "rembg")],
}


# 官方/文档已明确标注可互换、但文件名不同的成对模型。
# 这里只把「本地已有」挂成 candidate，仍要用户确认，不自动落盘。
EQUIVALENT_MODELS = {
    "dino_v3_l_naf_fp32.safetensors": "dino_v3_vit_l.safetensors",
    "dino_v3_vit_l.safetensors": "dino_v3_l_naf_fp32.safetensors",
}


# 真正缺失时的下载入口。精确文件优先；没有登记的文件走 HuggingFace 搜索。
MODEL_DOWNLOADS = {
    "flux3-image-klein-int8-convrot.safetensors": (
        "https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy",
        "HuggingFace 模型页：接受许可后下载 int8 权重",
    ),
    "dino_v3_l_naf_fp32.safetensors": (
        "https://huggingface.co/Comfy-Org/Pixal3D/resolve/main/clip_vision/dino_v3_L_naf_fp32.safetensors",
        "HuggingFace 直接下载 dino_v3_L_naf_fp32",
    ),
    # ---- 上次盘点「真缺且无直链」的 4 个文件（直链均已 HEAD 验证可下载） ----
    "acestep_v1.5_xl_turbo_bf16.safetensors": (
        "https://huggingface.co/Comfy-Org/ACE-Step-1.5/resolve/main/split_files/diffusion_models/acestep_v1.5_xl_turbo_bf16.safetensors",
        "ComfyUI 官方转制（music-ace-step15 变体的 diffusion_models，约 9.3GB）",
    ),
    "yue2_3b_int8_convrot.safetensors": (
        "https://huggingface.co/Comfy-Org/YuE2/resolve/main/checkpoints/yue2_3b_int8_convrot.safetensors",
        "ComfyUI 官方转制（music-yue2 变体的 checkpoints，约 3.7GB）",
    ),
    "qwen_2.5_vl_7b_fp8_scaled.safetensors": (
        "https://huggingface.co/fangg2000/qwen_2.5_vl_7b_fp8_scaled/resolve/main/qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "社区转制（layers 分层工作流的 text_encoders，约 8.7GB；无官方 fp8_scaled 版）",
    ),
    "qwen3vl_4b_int8_convrot.safetensors": (
        "https://huggingface.co/Merserk/qwen3vl-4b-int8-convrot/resolve/main/qwen3vl_4b_int8_convrot.safetensors",
        "社区转制（flux3-klein/krea2 的 text_encoders，约 4.5GB；官方只有 bf16/fp8_scaled 版）",
    ),
    "depth_anything_v2_vitl.pth": (
        "https://huggingface.co/depth-anything/Depth-Anything-V2-Large/resolve/main/depth_anything_v2_vitl.pth",
        "官方权重（mockup 贴图深度预处理器，约 1.3GB；comfyui_controlnet_aux 也会首次使用时自动下载）",
    ),
    # ---- MiniMax H3 文本编码器（nvfp4_awq 变体，ComfyArt 官方工作流默认引用） ----
    "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors": (
        "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "MiniMax H3 文本编码器（约 14.6GB，放 text_encoders/）；官方推荐优先用 int8_convrot 版本，本机已装 int8 时可直接换用",
    ),
}


def _enum_values(node_info: dict, field: str) -> list[str]:
    """兼容 ComfyUI 的两种下拉 schema：

    - 旧版: ["field", [["a", "b"]]] -> spec[0] 是列表
    - 新版: ["field", ["COMBO", {"options": ["a", "b"]}]]
    """
    req = (node_info.get("input") or {}).get("required") or {}
    spec = req.get(field)
    if not isinstance(spec, list) or not spec:
        return []
    # 旧版 [[...]] 或新版 [["COMBO", {"options": [...]}]]
    if isinstance(spec[0], list):
        if spec[0] and isinstance(spec[0][0], list):
            return list(spec[0][0])
        if (
            len(spec[0]) >= 2
            and spec[0][0] == "COMBO"
            and isinstance(spec[0][1], dict)
        ):
            return list(spec[0][1].get("options") or [])
        # 旧版/包装格式: [[...]] 或 [["a", "b"]]
        return list(spec[0])
    # 有些节点把 COMBO 放在第二项
    if (
        len(spec) >= 2
        and spec[0] == "COMBO"
        and isinstance(spec[1], dict)
    ):
        return list(spec[1].get("options") or [])
    return []



def build_registry(object_info: dict) -> dict[str, list[str]]:
    """从 /object_info 构造 {kind: [model_file, ...]}。"""
    registry: dict[str, set[str]] = {}
    for cls, fields in LOADERS.items():
        info = object_info.get(cls)
        if not info:
            continue
        for field, kind in fields:
            for name in _enum_values(info, field):
                registry.setdefault(kind, set()).add(name)
    return {k: sorted(v) for k, v in registry.items()}


def scan_models(graph: dict) -> list[dict]:
    """扫描工作流里所有模型引用。

    返回 [{node_id, class_type, field, kind, current}]
    """
    found: list[dict] = []
    for nid, node in graph.items():
        if not isinstance(node, dict):
            continue
        cls = node.get("class_type")
        fields = LOADERS.get(cls)
        if not fields:
            continue
        inputs = node.get("inputs") or {}
        for field, kind in fields:
            if field in inputs and isinstance(inputs[field], str):
                found.append({
                    "node_id": str(nid),
                    "class_type": cls,
                    "field": field,
                    "kind": kind,
                    "current": inputs[field],
                })
    return found


def diff(graph: dict, registry: dict[str, list[str]]) -> list[dict]:
    """给每条模型引用补上 available 列表、缺失标记和本地匹配建议。"""
    out = []
    for ref in scan_models(graph):
        available = registry.get(ref["kind"], [])
        ref["available"] = available
        ref["missing"] = ref["current"] not in available
        match = autofill.match_item(ref["current"], available)
        ref["status"] = match["status"]      # ok / auto / candidate / missing
        ref["matched"] = match["matched"]    # 建议写入的值；missing 时为 None
        ref["candidates"] = match["candidates"]
        ref["search_urls"] = match["search_urls"]

        # 官方文档标注可互换的别名文件：本地有就列为待确认候选。
        if ref["status"] == "missing":
            base = os.path.basename(ref["current"]).lower()
            alias = EQUIVALENT_MODELS.get(base)
            if alias:
                for local in available:
                    if os.path.basename(local).lower() == alias:
                        ref["status"] = "candidate"
                        ref["matched"] = local
                        ref["candidates"] = [{
                            "name": local,
                            "score": 1.0,
                            "reason": "ComfyUI 官方文档标注两者等效",
                        }]
                        break

        # 缺失项提供下载入口：已知精确地址优先，否则落到 HuggingFace 搜索。
        base = os.path.basename(ref["current"]).lower()
        exact = MODEL_DOWNLOADS.get(base)
        ref["download_url"] = exact[0] if exact else ref["search_urls"].get("huggingface", "")
        ref["download_note"] = exact[1] if exact else "在 HuggingFace 搜索同名模型"
        out.append(ref)

    return out


def apply_replacements(graph: dict, replacements: list[dict]) -> list[dict]:
    """把 replacements [{node_id, field, value}] 写回 graph。返回实际改动列表。"""
    changed = []
    for rep in replacements:
        nid = str(rep.get("node_id", ""))
        field = rep.get("field")
        value = rep.get("value")
        if nid not in graph or field is None:
            continue
        node = graph[nid]
        if not isinstance(node, dict):
            continue
        old = (node.get("inputs") or {}).get(field)
        node.setdefault("inputs", {})[field] = value
        changed.append({"node_id": nid, "field": field, "from": old, "to": value})
    return changed
