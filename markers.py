"""marker 解析、校验、参数填充。1:1 复刻自 ComfyArt src/customMarkers.ts + comfy.ts 的相关部分。

上游声明：marker 规范、校验与填充逻辑移植自 jtydhr88/ComfyArt
（https://github.com/jtydhr88/ComfyArt，MIT License，Copyright (c) ComfyArt 作者）。
本项目为独立适配层，与 ComfyArt 官方无关。

工作流是 ComfyUI API format: {node_id: {class_type, inputs, _meta:{title}}}
ComfyArt 通过节点 _meta.title 里的 "@xxx" 标记知道往哪个节点塞什么。
"""
import os
import random
from typing import Any, Optional

from actions import ACTIONS

# ---- marker 定义（id → 用途说明） ----
# 下面这 21 个与 ComfyArt src/customMarkers.ts 的 MARKERS 完全一致，保持 1:1 同步。
CORE_MARKERS: dict[str, str] = {
    "image": "LoadImage/LoadImageMask：输入图",
    "image2": "LoadImage：第二张图（风格参考）",
    "base": "LoadImage：img2img 底图",
    "front": "LoadImage：正视图",
    "left": "LoadImage：左视图",
    "back": "LoadImage：后视图",
    "right": "LoadImage：右视图",
    "mask": "LoadImageMask（channel=alpha）或 LoadImage 的 MASK 输出；白=要改的区域",
    "last": "LoadImage：视频最后一帧",
    "video": "LoadVideo：输入视频",
    "audio": "LoadAudio：输入录音",
    "prompt": "文本 widget 节点（CLIPTextEncode.text / String.value 等）",
    "lyrics": "歌词 widget 节点（YuE2/MiniMaxMusic3TextEncode/String.value）",
    "size": "同时带 width+height 的节点（EmptyLatentImage）",
    "width": "Int 节点或任意带 width 的 widget",
    "height": "Int 节点或任意带 height 的 widget",
    "count": "Int 或带 batch_size/amount 的节点：出图张数",
    "seconds": "Int/Float 或带 seconds/duration/max_duration：视频/音频时长（秒）",
    "strength": "Float 或带 denoise/strength：重绘幅度 0.1~1",
    "unet": "第一个输出是 MODEL 的节点（LoadDiffusionModel/LoadCheckpoint），Advanced 里的 LoRA 会串在它后面",
    "save": "输出节点（SaveImage/SaveVideo/PreviewImage），多出口时才需要",
}

# 作者上游后续新增、本项目已适配的标记（不在最初复刻的 21 个里）。
# @negative：qwen-image-2512 等新模型的负向提示词文本节点。
UPSTREAM_MARKERS: dict[str, str] = {
    "negative": "文本 widget 节点：负向提示词（qwen-image-2512 等）",
}

# 本项目扩展（不属于作者规范）：ComfyArt 给自定义工作流只留了 @image/@image2
# 两个图位，多图编辑（主图 + 2~3 张参考图）需要额外图位。
# 单独放一处，方便以后对照作者仓库的 src/customMarkers.ts 同步；
# 环境变量 COMFYART_EXTRA_MARKERS=0 可整体关掉，退回原版 21 个标记。
EXTRA_MARKERS: dict[str, str] = {
    "image3": "LoadImage：第三张图（本项目扩展，多图编辑用）",
    # 标题 @saveMask 会被 title.lower() 解析成 savemask，key 统一小写
    "savemask": "输出遮罩的保存节点（本项目扩展：mockup 贴图工作流里 @save 输出深度图、@saveMask 输出遮罩）",
}
ENABLE_EXTRA_MARKERS = os.environ.get("COMFYART_EXTRA_MARKERS", "1").strip().lower() \
    not in ("0", "false", "off", "no")
MARKERS: dict[str, str] = {
    **CORE_MARKERS, **UPSTREAM_MARKERS,
    **({**EXTRA_MARKERS} if ENABLE_EXTRA_MARKERS else {}),
}

LOADERS = {"LoadImage", "LoadImageMask"}
# 视频输入节点：除 ComfyUI 标准 LoadVideo 外，兼容 VHS 系列（输入键是 video 而不是 file）
VIDEO_LOADERS = {"LoadVideo", "VHS_LoadVideo", "VHS_LoadVideoPath",
                 "VHS_LoadVideoFFmpeg", "VHS_LoadVideoFFmpegPath"}
CORE_IMAGE_MARKERS = {"image", "image2", "base", "front", "left", "back", "right", "mask", "last"}
# 只有真正接收图片输入的扩展标记才进 IMAGE_MARKERS（savemask 是输出标记，不算）
IMAGE_MARKERS = CORE_IMAGE_MARKERS | ({"image3"} if ENABLE_EXTRA_MARKERS else set())
TEXT_KEYS = ["text", "prompt", "value", "string", "tags", "caption", "style", "target_text"]
LYRICS_KEYS = ["lyrics", "value", "text", "string"]
NUMBER_KEYS: dict[str, list[str]] = {
    "width": ["value", "width"],
    "height": ["value", "height"],
    "count": ["batch_size", "amount"],
    "seconds": ["seconds", "duration", "max_duration"],
    "strength": ["value", "denoise", "strength"],
}
NO_LYRICS = "[instrumental]"

PromptGraph = dict[str, dict[str, Any]]


# ---- 解析 ----
def title_tags(node: dict) -> list[str]:
    title = (node.get("_meta") or {}).get("title") or ""
    raw = title.lower().replace(",", " ").split()
    return [w for w in raw if w.startswith("@")]


def as_api_workflow(data: Any) -> PromptGraph:
    """校验是 API format（不是 UI format，也不是数组）。"""
    if not isinstance(data, dict) or not data or "nodes" in data:
        raise ValueError("不是 API format：请在 ComfyUI 用 Workflow > Export (API) 导出")
    for nid, node in data.items():
        if not isinstance(node, dict) or "class_type" not in node or "inputs" not in node:
            raise ValueError(f"节点 {nid} 缺少 class_type/inputs")
    return data


def find_markers(graph: PromptGraph) -> dict[str, str]:
    """返回 {marker: node_id}，每个 marker 只取第一个出现的节点。"""
    found: dict[str, str] = {}
    for nid, node in graph.items():
        for tag in title_tags(node):
            m = tag[1:]
            if m in MARKERS and m not in found:
                found[m] = nid
    return found


def _sink_of(graph: PromptGraph) -> Optional[str]:
    used: set[str] = set()
    for node in graph.values():
        for v in (node.get("inputs") or {}).values():
            if isinstance(v, list) and v and isinstance(v[0], str):
                used.add(v[0])
    sinks = [nid for nid in graph if nid not in used]
    return sinks[0] if len(sinks) == 1 else None


def _output_of(graph: PromptGraph, markers: dict[str, str]) -> Optional[str]:
    return markers.get("save") or _sink_of(graph)


def _text_key(node: dict, names: list[str] = TEXT_KEYS) -> Optional[str]:
    inputs = node.get("inputs") or {}
    keys = [k for k, v in inputs.items() if isinstance(v, str)]
    for k in names:
        if k in keys:
            return k
    return keys[0] if len(keys) == 1 else None


def _number_key(node: dict, marker: str) -> Optional[str]:
    inputs = node.get("inputs") or {}
    keys = [k for k, v in inputs.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    for k in NUMBER_KEYS.get(marker, []):
        if k in keys:
            return k
    return None


# ---- 校验 ----
def check_workflow(graph: PromptGraph, action: str) -> dict:
    spec = ACTIONS[action]
    markers = find_markers(graph)
    errors: list[str] = []
    warnings: list[str] = []

    def has(m: str) -> bool:
        return m in markers or (m == "size" and "width" in markers and "height" in markers)

    missing = [m for m in spec.required if not has(m)]
    if missing:
        pretty = ", ".join("@size (或 @width+@height)" if m == "size" else f"@{m}" for m in missing)
        errors.append(f"缺少必需标记: {pretty}")

    titles: dict[str, list[str]] = {}
    for nid, node in graph.items():
        for tag in title_tags(node):
            titles.setdefault(tag, []).append(nid)

    usable = set(spec.required) | set(spec.optional) | {"save"}
    if "size" in spec.required or "size" in spec.optional:
        usable |= {"width", "height"}
    if action == "mockup":
        usable.add("savemask")

    for tag, ids in titles.items():
        m = tag[1:]
        if len(ids) > 1:
            errors.append(f"标记 {tag} 重复出现在节点 {', '.join(ids)}")
        elif m not in MARKERS:
            warnings.append(f"未知标记 {tag}（节点 {ids[0]}）已忽略")
        elif m not in usable:
            warnings.append(f"标记 {tag} 对槽位 {action} 无意义")

    for m, nid in markers.items():
        if m not in usable:
            continue
        node = graph[nid]
        ct = node.get("class_type", "?")
        if m in IMAGE_MARKERS:
            if ct not in LOADERS:
                errors.append(f"@{m} 必须在 LoadImage/LoadImageMask 上，当前是 {ct} #{nid}")
        elif m == "video":
            if ct not in VIDEO_LOADERS:
                errors.append(f"@video 必须在 LoadVideo / VHS_LoadVideo 上，当前是 {ct} #{nid}")
        elif m == "audio":
            if ct != "LoadAudio":
                errors.append(f"@audio 必须在 LoadAudio 上，当前是 {ct} #{nid}")
        elif m in ("prompt", "negative"):
            if not _text_key(node):
                errors.append(f"@{m} 节点 {ct} #{nid} 没有文本 widget")
        elif m == "lyrics":
            if not _text_key(node, LYRICS_KEYS):
                errors.append(f"@lyrics 节点 {ct} #{nid} 没有歌词 widget")
        elif m == "size":
            inputs = node.get("inputs") or {}
            if not (isinstance(inputs.get("width"), (int, float))
                    and isinstance(inputs.get("height"), (int, float))):
                errors.append(f"@size 节点 {ct} #{nid} 需要 width 和 height 数值")
        elif m in ("save", "savemask", "unet"):
            pass
        else:
            if not _number_key(node, m):
                errors.append(f"@{m} 节点 {ct} #{nid} 没有对应数值 widget")

    out = _output_of(graph, markers)
    if not out:
        errors.append("无法确定输出节点：工作流有多个出口，请在结果节点上标 @save")
    else:
        out_inputs = (graph[out].get("inputs") or {})
        if spec.needs_images and not isinstance(out_inputs.get("images"), list):
            errors.append(f"输出节点 {out}（{graph[out].get('class_type')}）需要 images 输出")
        if spec.needs_frames and not isinstance(out_inputs.get("images"), list) \
                and not isinstance(out_inputs.get("video"), list):
            errors.append(f"输出节点 {out} 需要 video 或 images 输出")

    return {"markers": markers, "errors": errors, "warnings": warnings}


# ---- 填充 ----
def chain_loras(graph: PromptGraph, unet_id: str, loras: list[dict]) -> str:
    """在 @unet 节点后串接 LoraLoader 链，返回最后一级 LoRA 的 node_id。

    loras: [{"name": "xxx.safetensors", "strength": 0.8}, ...]
    - 自动识别原节点是否被引用了 CLIP（端口 1），决定链是否走 clip
    - 原引用 unet 的 MODEL/CLIP 全部改指向链尾
    """
    if not loras:
        return unet_id
    # 找所有引用 unet 节点的位置
    refs: list[tuple[str, str, int]] = []
    for nid, node in graph.items():
        for k, v in (node.get("inputs") or {}).items():
            if isinstance(v, list) and len(v) == 2 and v[0] == unet_id:
                refs.append((nid, k, int(v[1])))
    uses_clip = any(slot == 1 for _, _, slot in refs)

    prev_id = unet_id
    for i, lora in enumerate(loras):
        strength = float(lora.get("strength", 1.0))
        name = lora["name"]
        # 生成不冲突的 id
        lora_id = f"_lora_{i}"
        while lora_id in graph:
            lora_id += "_"
        # 只有 @unet 节点本身带 CLIP 输出（如 CheckpointLoaderSimple）时才走 LoraLoader；
        # 否则（UNETLoader / LoraLoaderModelOnly 这类纯模型加载器）改用 LoraLoaderModelOnly，
        # 否则 ComfyUI 会因为缺少必填的 clip 输入报 "Required input is missing"。
        inputs: dict[str, Any] = {
            "model": [prev_id, 0],
            "lora_name": name,
            "strength_model": strength,
        }
        if uses_clip:
            inputs["clip"] = [prev_id, 1]
            inputs["strength_clip"] = strength
        graph[lora_id] = {
            "class_type": "LoraLoader" if uses_clip else "LoraLoaderModelOnly",
            "inputs": inputs,
        }
        prev_id = lora_id

    # 原引用改指向链尾；只改 MODEL/CLIP，VAE 等其他输出（如 CheckpointLoader 的 slot 2）保持原样
    slots = {0, 1} if uses_clip else {0}
    for nid, k, slot in refs:
        if slot in slots:
            graph[nid]["inputs"][k] = [prev_id, slot]
    return prev_id


def fill_workflow(graph: PromptGraph, markers: dict[str, str], values: dict) -> str:
    """把 values 填进对应节点，随机化所有 seed。返回输出节点 id。"""
    loras = values.get("loras") or []
    for m, nid in markers.items():
        if m in ("size", "save", "savemask", "unet"):
            continue
        value = values.get(m)
        if value is None:
            continue
        node = graph[nid]
        inputs = node.setdefault("inputs", {})
        if m in IMAGE_MARKERS:
            inputs["image"] = value
            if node.get("class_type") == "LoadImageMask":
                inputs["channel"] = "alpha"
        elif m == "video":
            # VHS 系列节点的输入键是 video，ComfyUI 标准 LoadVideo 是 file
            key = "video" if str(node.get("class_type", "")).startswith("VHS_LoadVideo") else "file"
            inputs[key] = value
        elif m == "audio":
            inputs["audio"] = value
        elif m == "lyrics":
            k = _text_key(node, LYRICS_KEYS)
            if k:
                inputs[k] = value or NO_LYRICS
        elif m in ("prompt", "negative"):
            k = _text_key(node)
            if k:
                inputs[k] = value
        else:
            k = _number_key(node, m)
            if k:
                inputs[k] = value

    if "size" in markers and values.get("width") and values.get("height"):
        size_inputs = graph[markers["size"]].setdefault("inputs", {})
        size_inputs["width"] = values["width"]
        size_inputs["height"] = values["height"]
        if values.get("count") and isinstance(size_inputs.get("batch_size"), (int, float)) \
                and "count" not in markers:
            size_inputs["batch_size"] = values["count"]

    # @unet 链式 LoRA
    if "unet" in markers and loras:
        chain_loras(graph, markers["unet"], loras)

    for node in graph.values():
        for k in ("seed", "noise_seed"):
            if isinstance((node.get("inputs") or {}).get(k), (int, float)):
                node["inputs"][k] = random.randint(0, 2**31 - 1)

    return _output_of(graph, markers)  # type: ignore[return-value]
