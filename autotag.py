"""按节点语义自动补 _meta.title 里的 @marker。

背景：ComfyUI 用 Workflow > Export (API) 导出的 JSON 不带 _meta.title，
而 ComfyArt 的槽位要求节点标题里必须有 @prompt / @size / @image 等标记。
人工一个个改太慢，这里按节点类型 + 上下游接线关系自动推断。

设计原则：
- 只加不收：已有标记一律保留，只补缺失的。
- 只加能通过 markers.check_workflow 的标记（数值类先确认节点真的有对应 widget）。
- 推断不出来的不硬塞，作为 unresolved 交回前端提示。
"""
from __future__ import annotations

from typing import Any, Optional

from actions import ACTIONS
from markers import (
    IMAGE_MARKERS,
    TEXT_KEYS,
    LYRICS_KEYS,
    title_tags,
    _text_key,
    _number_key,
    check_workflow,
)

Graph = dict[str, dict[str, Any]]

# 模型加载类节点（输出 0 = MODEL）
MODEL_CLASSES = {
    "UNETLoader",
    "UnetLoaderGGUF",
    "UnetLoaderGGUFAdvanced",
    "CheckpointLoaderSimple",
    "CheckpointLoader",
    "LoadDiffusionModel",
    "LoadCheckpoint",
    "FluxCheckpointLoader",
    "WanVideoModelLoader",
}
MODEL_NAME_KEYS = ("unet_name", "ckpt_name", "model_name")


# ---------- 小工具 ----------
def _sort_key(nid: str) -> tuple[int, str]:
    try:
        return (int(nid), nid)
    except ValueError:
        return (10**9, nid)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _inputs(node: dict) -> dict:
    return node.get("inputs") or {}


def _ref_src(graph: Graph, ref: Any) -> Optional[str]:
    """输入值是 [node_id, slot] 时返回 node_id。"""
    if isinstance(ref, list) and len(ref) == 2 and isinstance(ref[0], str):
        return ref[0]
    return None


def _upstream(graph: Graph, start: Optional[str], limit: int = 64) -> set[str]:
    """从 start 节点往上（往输入方向）走，收集涉及的节点 id（含 start）。"""
    if not start or start not in graph:
        return set()
    seen: set[str] = set()
    stack = [start]
    while stack and len(seen) < limit:
        cur = stack.pop()
        if cur in seen or cur not in graph:
            continue
        seen.add(cur)
        for v in _inputs(graph[cur]).values():
            src = _ref_src(graph, v)
            if src:
                stack.append(src)
    return seen


def _text_like(graph: Graph, nid: str) -> Optional[str]:
    """是文本 widget 节点则返回该 widget 的键名。

    键名必须和 markers._text_key() 的判断一致，否则标了也填不进去。
    """
    node = graph[nid]
    key = _text_key(node)
    if not key:
        return None
    low = key.lower()
    # 文件名、负向提示词、枚举名都不算 prompt
    if "negative" in low or "prefix" in low:
        return None
    if low.endswith(("_name", "_file", "_path", "_mode", "_method", "_type")):
        return None
    if low in ("image", "file", "audio", "video", "filename"):
        return None
    # 只认明确的文本字段，避免把 filename_prefix / sampler_name 之类当成提示词
    if not (key in TEXT_KEYS or low.endswith(("prompt", "text", "caption", "lyrics", "script"))):
        return None
    return key


# ---------- 各类标记的推断 ----------
def _sampler_ids(graph: Graph) -> list[str]:
    out = []
    for nid, node in graph.items():
        ins = _inputs(node)
        if "positive" in ins or ("guider" in ins and "sampler" in ins):
            out.append(nid)
    return sorted(out, key=_sort_key)


def _pick_prompt(graph: Graph, taken: set[str], strict: bool = False) -> Optional[str]:
    """优先取 positive 条件链上的文本节点；strict=True 时只认 positive 链。"""
    samplers = _sampler_ids(graph)
    positives: set[str] = set()
    negatives: set[str] = set()
    for sid in samplers:
        ins = _inputs(graph[sid])
        positives |= _upstream(graph, _ref_src(graph, ins.get("positive")))
        # SamplerCustomAdvanced 的 positive 在 guider 里
        guid = _ref_src(graph, ins.get("guider"))
        if guid and guid in graph:
            g_ins = _inputs(graph[guid])
            positives |= _upstream(graph, _ref_src(graph, g_ins.get("positive")))
            negatives |= _upstream(graph, _ref_src(graph, g_ins.get("negative")))
        negatives |= _upstream(graph, _ref_src(graph, ins.get("negative")))
    negatives -= positives

    cands: list[tuple[int, tuple[int, str], str]] = []
    for nid in graph:
        if nid in taken:
            continue
        key = _text_like(graph, nid)
        if not key:
            continue
        if nid in negatives:
            continue
        if strict and nid not in positives:
            continue
        score = 0
        if nid in positives:
            score += 12
        ct = (graph[nid].get("class_type") or "").lower()
        if "textencode" in ct or "textenc" in ct:
            score += 3
        if key in ("text", "prompt", "caption"):
            score += 2
        # 文本越长越像正式提示词
        score += min(len(str(_inputs(graph[nid]).get(key) or "")) // 200, 3)
        cands.append((-score, _sort_key(nid), nid))

    cands.sort(key=lambda t: (t[0], t[1]))
    return cands[0][2] if cands else None


def _pick_lyrics(graph: Graph, taken: set[str]) -> Optional[str]:
    best: Optional[tuple[int, tuple[int, str], str]] = None
    for nid in graph:
        if nid in taken:
            continue
        key = _text_like(graph, nid)
        if not key:
            continue
        inputs = _inputs(graph[nid])
        score = 0
        if "lyric" in key.lower():
            score += 8
        ct = (graph[nid].get("class_type") or "").lower()
        if "lyric" in ct or "yue" in ct or "music" in ct:
            score += 4
        if key in LYRICS_KEYS:
            score += 2
        if score <= 2:
            continue
        cand = (-score, _sort_key(nid), nid)
        if best is None or cand < best:
            best = cand
    return best[2] if best else None


def _pick_size(graph: Graph, taken: set[str]) -> Optional[tuple[str, str]]:
    """返回 (node_id, 'size'|'wh')；节点需同时带数值 width/height。"""
    cands: list[tuple[int, tuple[int, str], str]] = []
    for nid, node in graph.items():
        ins = _inputs(node)
        if _is_num(ins.get("width")) and _is_num(ins.get("height")):
            score = 0
            ct = (node.get("class_type") or "").lower()
            if "empty" in ct or "latent" in ct:
                score += 5
            if "size" in ct:
                score += 2
            cands.append((-score, _sort_key(nid), nid))
    if not cands:
        return None
    cands.sort(key=lambda t: (t[0], t[1]))
    return cands[0][2], "size"


def _resolve_numeric_src(graph: Graph, ref: Any, marker: str, depth: int = 0) -> Optional[str]:
    """沿引用链向下找真正带数值 widget 的节点。

    兼容 ImageResizeKJv2 这种 width/height 先接 ComfyMathExpression（如 `(a // 32) * 32`）、
    再接到 PrimitiveInt 的结构；返回最底层可写值的节点 id。
    """
    if depth > 4:
        return None
    src = _ref_src(graph, ref)
    if not src or src not in graph:
        return None
    node = graph[src]
    if _number_key(node, marker):
        return src
    for value in _inputs(node).values():
        if isinstance(value, list) and len(value) == 2:
            found = _resolve_numeric_src(graph, value, marker, depth + 1)
            if found:
                return found
    return None


def _pick_size_split(graph: Graph, taken: set[str]) -> tuple[Optional[str], Optional[str]]:
    """width/height 由两个独立 Int 节点提供时的兜底推断。"""
    w: Optional[str] = None
    h: Optional[str] = None
    for nid, node in graph.items():
        ins = _inputs(node)
        if _is_num(ins.get("width")) or _is_num(ins.get("height")):
            continue  # 这种节点本身就能整块标 size
        for key, target in (("width", "w"), ("height", "h")):
            src = _resolve_numeric_src(graph, ins.get(key), key)
            if not src or src not in graph:
                continue
            if src in taken:
                continue
            if target == "w" and not w:
                w = src
            if target == "h" and not h and src != w:
                h = src
        if w and h:
            break
    return w, h


def _pick_number(graph: Graph, marker: str, taken: set[str], prefer: set[str] | None = None) -> Optional[str]:
    """找带对应数值 widget 的节点（用 markers 的键表，保证校验能过）。"""
    cands: list[tuple[int, tuple[int, str], str]] = []
    for nid, node in graph.items():
        if nid in taken:
            continue
        if not _number_key(node, marker):
            continue
        score = 0
        if prefer and (node.get("class_type") or "") in prefer:
            score += 4
        if marker == "strength":
            ct = (node.get("class_type") or "").lower()
            if "sampler" in ct:
                score += 4
        if marker == "count":
            ins = _inputs(node)
            if _is_num(ins.get("batch_size")):
                score += 3
        cands.append((-score, _sort_key(nid), nid))
    cands.sort(key=lambda t: (t[0], t[1]))
    return cands[0][2] if cands else None


def _pick_model(graph: Graph, taken: set[str]) -> Optional[str]:
    cands: list[tuple[int, tuple[int, str], str]] = []
    for nid, node in graph.items():
        if nid in taken:
            continue
        ct = node.get("class_type") or ""
        ins = _inputs(node)
        score = 0
        if ct in MODEL_CLASSES:
            score += 6
        elif ct.lower().endswith("loader") and any(k in ins for k in MODEL_NAME_KEYS):
            score += 3
        if any(k in ins for k in MODEL_NAME_KEYS):
            score += 2
        if score < 2:
            continue
        cands.append((-score, _sort_key(nid), nid))
    cands.sort(key=lambda t: (t[0], t[1]))
    return cands[0][2] if cands else None


def _pick_loaders(graph: Graph, cls: str, taken: set[str]) -> list[str]:
    return [nid for nid in sorted(graph, key=_sort_key)
            if nid not in taken and graph[nid].get("class_type") == cls]


VIDEO_LOADER_CLASSES = ("LoadVideo", "VHS_LoadVideo", "VHS_LoadVideoPath",
                        "VHS_LoadVideoFFmpeg", "VHS_LoadVideoFFmpegPath")


def _pick_video_loaders(graph: Graph, taken: set[str]) -> list[str]:
    """视频输入节点：标准 LoadVideo + VHS 系列（VHS_LoadVideo 等）。"""
    return [nid for nid in sorted(graph, key=_sort_key)
            if nid not in taken
            and graph[nid].get("class_type") in VIDEO_LOADER_CLASSES]


def _used_refs(graph: Graph) -> tuple[set[str], set[tuple[str, int]]]:
    """返回 (被引用的节点集合, 被引用的 (节点, 输出槽) 集合)。"""
    nodes: set[str] = set()
    slots: set[tuple[str, int]] = set()
    for node in graph.values():
        for v in _inputs(node).values():
            src = _ref_src(graph, v)
            if src:
                nodes.add(src)
                if isinstance(v, list) and len(v) == 2 and isinstance(v[1], int):
                    slots.add((src, v[1]))
    return nodes, slots


def _pick_mask(graph: Graph, taken: set[str]) -> Optional[str]:
    """@mask 只能落在 LoadImageMask / LoadImage 上，且不要和 @image 抢同一个节点。"""
    masks = _pick_loaders(graph, "LoadImageMask", set())
    if masks:
        return masks[0]
    # LoadImage 的 MASK 输出（槽 1）确实被下游用了，才算可作 mask 用
    _, slots = _used_refs(graph)
    for nid in _pick_loaders(graph, "LoadImage", set()):
        if (nid, 1) in slots and nid not in taken:
            return nid
    return None


def _pick_save(graph: Graph, taken: set[str]) -> Optional[str]:
    used, _ = _used_refs(graph)
    sinks = [nid for nid in graph if nid not in used and nid not in taken]
    outs = [nid for nid in sinks
            if "save" in (graph[nid].get("class_type") or "").lower()
            or "preview" in (graph[nid].get("class_type") or "").lower()]
    if len(outs) >= 1 and len(sinks) > 1:
        return sorted(outs, key=_sort_key)[0]
    return None


# ---------- 主入口 ----------
IMAGE_ORDER = ("image", "image2", "image3", "base", "last")
VIEW_ORDER = ("front", "left", "back", "right")


def suggest(graph: Graph, action: str) -> dict:
    spec = ACTIONS[action]
    allowed = set(spec.required) | set(spec.optional) | {"save"}
    if "size" in allowed:
        allowed |= {"width", "height"}

    existing = {m: nid for m, nid in _existing_markers(graph).items() if m in allowed}
    taken: set[str] = set(existing.values())
    found: dict[str, str] = dict(existing)
    changes: list[dict] = []

    def put(marker: str, nid: Optional[str], reason: str) -> None:
        if not nid or marker in found or marker not in allowed:
            return
        found[marker] = nid
        taken.add(nid)
        node = graph[nid]
        changes.append({
            "marker": marker,
            "node_id": nid,
            "class_type": node.get("class_type", "?"),
            "reason": reason,
        })

    # 1) 文本类
    if "prompt" in allowed:
        strict = "prompt" not in spec.required
        put("prompt", _pick_prompt(graph, taken, strict=strict),
            "positive 条件链上的文本编码节点")
    if "lyrics" in allowed:
        put("lyrics", _pick_lyrics(graph, taken), "歌词类文本节点")

    # 2) 输入媒体
    if "video" in allowed:
        put("video", next(iter(_pick_video_loaders(graph, taken)), None),
            "LoadVideo / VHS_LoadVideo 节点")
    if "audio" in allowed:
        put("audio", next(iter(_pick_loaders(graph, "LoadAudio", taken)), None), "LoadAudio 节点")
    if "mask" in allowed:
        put("mask", _pick_mask(graph, taken), "LoadImageMask / 带 MASK 输出的 LoadImage")

    imgs = _pick_loaders(graph, "LoadImage", taken)
    order = VIEW_ORDER if "front" in allowed else IMAGE_ORDER
    for name in order:
        if name not in allowed or not imgs:
            break
        if name in found:
            continue
        put(name, imgs.pop(0), f"LoadImage #{name} 输入位（按节点顺序分配）")

    # 3) 尺寸
    if "size" in allowed:
        size = _pick_size(graph, taken)
        if size:
            put("size", size[0], "同时带 width/height 的节点")
        else:
            w, h = _pick_size_split(graph, taken)
            put("width", w, "width 输入端引用的 Int 节点")
            put("height", h, "height 输入端引用的 Int 节点")

    # 4) 数值类（strength 只在必需时才动，避免改掉原 denoise 语义）
    #    数值标记可以和 @size 共用同一节点（EmptyLatentImage 同时带 width/height/batch_size）
    if "count" in allowed:
        put("count", _pick_number(graph, "count", set(), {"EmptyLatentImage"}), "batch_size 数量节点")
    if "seconds" in allowed:
        put("seconds", _pick_number(graph, "seconds", taken), "时长数值节点")
    if "strength" in spec.required:
        put("strength", _pick_number(graph, "strength", set(), {"KSampler"}), "denoise 重绘幅度")
    if "unet" in allowed:
        put("unet", _pick_model(graph, taken), "模型加载节点（输出 MODEL）")

    # 5) 多出口时标 @save
    put("save", _pick_save(graph, taken), "多出口，标在保存节点")

    tagged = apply_markers(graph, found)
    checked = check_workflow(tagged, action)
    missing = [e for e in checked["errors"] if "缺少必需标记" in e]
    satisfied = set(found)
    if "width" in satisfied and "height" in satisfied:
        satisfied.add("size")
    unresolved = sorted(set(spec.required) - satisfied)
    return {
        "markers": found,
        "changes": changes,
        "errors": checked["errors"],
        "warnings": checked["warnings"],
        "workflow": tagged,
        "unresolved": unresolved,
        "missing_error": missing[0] if missing else None,
    }


def _existing_markers(graph: Graph) -> dict[str, str]:
    out: dict[str, str] = {}
    for nid, node in graph.items():
        for tag in title_tags(node):
            out.setdefault(tag[1:], nid)
    return out


def apply_markers(graph: Graph, markers: dict[str, str]) -> Graph:
    """把 markers 写进 _meta.title（保留原有标题与其它标记），返回同一份 dict 的深拷贝。"""
    import copy

    out = copy.deepcopy(graph)
    for marker, nid in markers.items():
        node = out.get(nid)
        if node is None:
            continue
        meta = node.setdefault("_meta", {})
        title = meta.get("title") or node.get("class_type", "")
        words = [w for w in title.replace(",", " ").split() if w]
        if any(w.lower() == f"@{marker}" for w in words):
            continue
        words.append(f"@{marker}")
        meta["title"] = " ".join(words)
    return out
