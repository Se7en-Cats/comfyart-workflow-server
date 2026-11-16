# -*- coding: utf-8 -*-
"""
一键回填 · 平台无关核心算法（零第三方依赖）

本文件是 model-autofill-kit 的算法内核（来源：用户提供的 `model-autofill-kit`），
已剥离全部宿主框架依赖，可直接拷入任何语言生态参考实现，或作为 Python 项目的独立模块使用。
只通过 `models.diff()` 调用 `match_item()`；不改 ComfyArt 作者代码。

职责：把「某台机器上记录的模型引用名」与「本机某类别下的实际文件清单」
做匹配，判定四类状态：ok / auto / candidate / missing。

设计原则：
1. 纯字符串处理，不碰文件系统、不碰网络、不碰 UI。
2. 判断"是不是同一个模型"与"能不能自动改"严格分开：
   - auto 才允许无人工确认直接改；
   - candidate 必须人工确认，因为精度/量化/版本可能不同。
3. 宁可漏判（判 missing 让用户下载），不可错配（把不兼容的模型写进画布）。

使用方式::

    from core.autofill_core import match_item, reference_candidates
    result = match_item("sub/a.safetensors", ["a.safetensors", "b.safetensors"])
"""

import os
import re
import difflib
from urllib.parse import quote_plus

# --------------------------------------------------------------------------
# 一、常量：改这些就能适配不同平台的命名习惯
# --------------------------------------------------------------------------

# 视为"模型文件"的扩展名。宿主新增格式时在这里加。
MODEL_EXTENSIONS = {
    ".ckpt", ".pt", ".pt2", ".bin", ".pth", ".safetensors",
    ".pkl", ".sft", ".gguf",
}

# 精度 / 量化词：分词阶段剔除，使 bf16 / fp16 / int8 / q4_k_m 视为同一基座。
# 注意：从 token 层面剔除，不是字符串硬删除，避免误伤主名（如 fp16x2）。
PRECISION_TOKENS = {
    "fp16", "fp32", "bf16", "bf32", "fp8", "f16", "f32",
    "fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2",
    "int8", "int4", "8bit", "4bit",
    "nvfp4", "fp4", "mxfp4", "awq", "w4a16", "w8a16",
    "q8", "q8_0", "q4", "q4_0", "q4_k_m", "q4_k_s",
    "q5", "q5_k_m", "q5_k_s", "q6", "q6_k", "q3_k_m", "q2_k",
    "gguf", "quant", "quantized",
}

# 结构 / 裁剪变体词：同属"同一基座的不同打包"，同样剔除。
STRUCTURE_TOKENS = {
    "pruned", "prune", "convrot", "convrotv2",
}

# 本地候选阈值：低于此分不列为候选，直接判 missing。
# 0.72 是实测折中值：过低会把同系列不同模型混进来，过高会漏掉真实变体。
CANDIDATE_THRESHOLD = 0.72

# 每个待确认项最多返回的候选数。
MAX_CANDIDATES = 3

# 互斥变体组：同组内命中不同成员 → 判为不同产品线，重罚。
# 这是防止"跨类型误配"的核心规则，例如基础版 base 与微调版 sft、
# 首尾帧 fl2va 与参考 ref2va，词面再像也不能互相替代。
VARIANT_GROUPS = [
    {"base", "sft"},
    {"base", "refiner"},
    {"t2i", "i2i", "txt2img", "img2img"},
    {"fl2va", "ref2va"},
]

# 产品线别名簇：同一产品线在不同打包方手里的不同叫法。
# 只用于"参考推荐"归线，不参与"是否同一模型"的判定。
PRODUCT_LINES = {
    "fl2va": {"fl2va", "fl2v"},
    "ref2va": {"ref2va", "ref2v", "refdelta"},
}
PRODUCT_LINE_TOKENS = {t for members in PRODUCT_LINES.values() for t in members}

# 常见 loader 节点 → (模型 widget 名, 落点类别)。
# 这是宿主适配数据，不是算法；换平台时替换成对方的资源字段映射即可。
NODE_MODEL_WIDGETS = {
    "CheckpointLoaderSimple": [("ckpt_name", "checkpoints")],
    "CheckpointLoader": [("ckpt_name", "checkpoints")],
    "LoraLoader": [("lora_name", "loras")],
    "LoraLoaderModelOnly": [("lora_name", "loras")],
    "VAELoader": [("vae_name", "vae")],
    "ControlNetLoader": [("control_net_name", "controlnet")],
    "DiffControlNetLoader": [("control_net_name", "controlnet")],
    "UNETLoader": [("unet_name", "diffusion_models")],
    "CLIPLoader": [("clip_name", "text_encoders")],
    "DualCLIPLoader": [
        ("clip_name1", "text_encoders"),
        ("clip_name2", "text_encoders"),
    ],
    "CLIPVisionLoader": [("clip_name", "clip_vision")],
    "StyleModelLoader": [("style_model_name", "style_models")],
    "GLIGENLoader": [("gligen_name", "gligen")],
    "UpscaleModelLoader": [("model_name", "upscale_models")],
    "HypernetworkLoader": [("hypernetwork_name", "hypernetworks")],
    "PhotoMakerLoader": [("photomaker_model_name", "photomaker")],
    "AudioEncoderLoader": [("audio_encoder_name", "audio_encoders")],
    "LatentUpscaleModelLoader": [("model_name", "latent_upscale_models")],
    "LoadBackgroundRemovalModel": [("bg_removal_name", "background_removal")],
    "DownloadAndLoadGIMMVFIModel": [("model", "interpolation")],
}

# --------------------------------------------------------------------------
# 二、分词与归一化
# --------------------------------------------------------------------------

_HASH_PREFIX_RE = re.compile(r"^\[[^\]]*\]")
_SEPARATOR_RE = re.compile(r"[_\-\.\(\)\[\]\s]+")
# 驼峰边界：DasiwaMinimaxH3 / dasiwaHybridV2 这类品牌前缀粘连名必须拆开，
# 否则品牌词与系列词完全不相交，相似度会假性偏低。
_CAMEL_RE_1 = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_RE_2 = re.compile(r"([A-Z]+)([A-Z][a-z])")


def file_ext(name: str) -> str:
    """返回小写扩展名（含点）；无扩展名返回空串。"""
    return os.path.splitext(name)[1].lower()


def is_model_file_name(name: str) -> bool:
    """按扩展名启发式判断某个选项是否是模型文件。"""
    return file_ext(name) in MODEL_EXTENSIONS


def tokenize(name: str) -> list:
    """
    把模型名拆成特征词列表。

    处理顺序：去目录 → 去扩展名 → 去 [hash] 前缀 → 驼峰拆分 →
    小写 + 分隔符切分 → 剔除精度 / 结构词。
    """
    if not name:
        return []
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = _HASH_PREFIX_RE.sub("", stem)
    stem = _CAMEL_RE_1.sub(r"\1_\2", stem)
    stem = _CAMEL_RE_2.sub(r"\1_\2", stem)
    tokens = [t for t in _SEPARATOR_RE.split(stem.lower()) if t]
    return [t for t in tokens
            if t not in PRECISION_TOKENS and t not in STRUCTURE_TOKENS]


def normalize(name: str) -> str:
    """归一化成以空格连接的词串，用于跨精度比较与展示。"""
    return " ".join(tokenize(name))


# --------------------------------------------------------------------------
# 三、变体与产品线判定
# --------------------------------------------------------------------------

def product_line(tokens: set) -> str | None:
    """token 集合所属的标准产品线；不属于任何已知线返回 None。"""
    for line, members in PRODUCT_LINES.items():
        if tokens & members:
            return line
    return None


def _is_anchor_token(tok: str) -> bool:
    """
    锚点词必须长度 ≥2 且非纯数字。

    单字母（r / v / f）与纯数字（3 / 7 / 2.2）的重合是偶然的，
    不能作为"同系列"的依据——否则 gimmvfi_r 会撞上 bernini_r。
    """
    return len(tok) >= 2 and not tok.isdigit()


def _variant_conflict(ot: list, ct: list) -> bool:
    """
    双方在同一互斥组里命中互不相同的成员 → 冲突。

    候选若同时包含 orig 的成员（如 hybrid 双类型模型同时带 fl2va/ref2va），
    视为兼容而不是冲突。
    """
    o, c = set(ot), set(ct)
    for group in VARIANT_GROUPS:
        oh, ch = o & group, c & group
        if oh and ch and not (oh & ch):
            return True
    return False


def _same_line_compatible(ot: set, ct: set) -> bool:
    """候选与 orig 是否同一兼容产品线。"""
    oline = product_line(ot)
    if not oline:
        return False
    if ct & PRODUCT_LINES[oline]:
        return True
    return product_line(ct) == oline


# --------------------------------------------------------------------------
# 四、相似度（核心公式）
# --------------------------------------------------------------------------

def similarity(orig_name: str, cand_name: str) -> float:
    """
    综合相似度，返回 [0, 1]。

    组成：
      overlap        = 0.5 * Jaccard + 0.5 * 召回率
                       （召回率避免"候选词更多"时被 Jaccard 过度压低）
      subset_bonus   = 0.1  一方词集是另一方子集时加分
      conflict_penalty = 0.15  互斥变体冲突时扣分
      char_ratio     = difflib 字符相似度，兜底处理拼写差异

      score = 0.6 * overlap + 0.4 * char_ratio + subset_bonus - conflict_penalty
    """
    ot, ct = tokenize(orig_name), tokenize(cand_name)
    if not ot or not ct:
        return 0.0
    o, c = set(ot), set(ct)
    inter = len(o & c)
    union = o | c
    jaccard = inter / len(union) if union else 0.0
    recall = inter / len(o) if o else 0.0
    overlap = 0.5 * jaccard + 0.5 * recall
    subset_bonus = 0.1 if (o <= c or c <= o) else 0.0
    conflict_penalty = 0.15 if _variant_conflict(ot, ct) else 0.0
    char_ratio = difflib.SequenceMatcher(
        None, " ".join(ot), " ".join(ct)).ratio()
    score = 0.6 * overlap + 0.4 * char_ratio + subset_bonus - conflict_penalty
    return max(0.0, min(1.0, score))


# 兼容旧名（与内部实现保持同名，方便对照）
_similarity = similarity


# --------------------------------------------------------------------------
# 五、单条匹配（状态机核心）
# --------------------------------------------------------------------------

def search_urls(original: str) -> dict:
    """按清洗后的型号词生成各平台检索入口（工作流本身不含官方来源）。"""
    tokens = tokenize(original or "")
    if tokens:
        query = " ".join(tokens)
    else:
        stem = os.path.splitext(os.path.basename(original or ""))[0]
        query = _HASH_PREFIX_RE.sub("", stem).strip()
    if not query:
        return {"civitai": "", "huggingface": "", "modelscope": "", "github": ""}
    q = quote_plus(query)
    return {
        "civitai": f"https://civitai.com/search/models?query={q}",
        "huggingface": f"https://hf-mirror.com/models?search={q}",
        "modelscope": f"https://modelscope.cn/search?search={q}",
        "github": f"https://github.com/search?q={q}&type=repositories",
    }


def match_item(original: str, local_list: list) -> dict:
    """
    对单个模型引用做匹配。

    :param original: 画布/工程里记录的模型名或相对路径
    :param local_list: 本机该类别下的模型相对路径列表
    :return: {
        original, status, matched, candidates, search_urls
      }
      status ∈ {ok, auto, candidate, missing}
    """
    result = {
        "original": original,
        "status": "missing",
        "matched": None,
        "candidates": [],
        "search_urls": search_urls(original or ""),
    }
    if not original or not isinstance(original, str):
        return result

    original = original.strip()
    local_list = local_list or []

    # 阶段 1：原值完全命中 → 无需处理
    if original in local_list:
        result["status"] = "ok"
        result["matched"] = original
        return result

    orig_base = os.path.basename(original)

    # 阶段 2：文件名（含扩展名）完全一致，仅子目录不同 → 可自动回填
    # 注意必须带扩展名比较：同基座的 .gguf 与 .safetensors 不是一回事。
    same_basename = [x for x in local_list
                     if os.path.basename(x) == orig_base]
    if same_basename:
        result["status"] = "auto"
        result["matched"] = same_basename[0]
        result["candidates"] = [{"name": same_basename[0], "score": 1.0}]
        return result

    # 阶段 3：模糊匹配，产出候选
    ot = set(tokenize(original))
    strong, fallback = [], []
    for local in local_list:
        score = similarity(original, local)
        ct = set(tokenize(local))
        shared = (ot & ct) - PRODUCT_LINE_TOKENS
        if score >= CANDIDATE_THRESHOLD:
            strong.append({"name": local, "score": round(score, 3)})
        elif shared and _same_line_compatible(ot, ct):
            # 同产品线 + 共享品牌锚点，但词面分不足：
            # 列入"同系列替代"，必须人工确认，绝不自动回填。
            fallback.append(
                {"name": local, "score": round(score, 3), "line_match": True})
    strong.sort(key=lambda x: -x["score"])
    fallback.sort(key=lambda x: -x["score"])
    scored = (strong + fallback)[:MAX_CANDIDATES]

    if scored:
        result["status"] = "candidate"
        result["matched"] = scored[0]["name"]
        result["candidates"] = scored
    else:
        result["status"] = "missing"
    return result


# --------------------------------------------------------------------------
# 六、缺失时的"同系列官方版本"参考推荐
# --------------------------------------------------------------------------

def reference_candidates(original: str, category: str, entries: list,
                         limit: int = 4) -> list:
    """
    从注册表 / 目录索引里挑"同基座 + 同产品线"的可替代版本供人工核对。
    这些条目只读展示，不参与一键回填。

    过滤条件：
      1. category 一致（category 为空则不过滤）
      2. 排除同名文件
      3. 双方都能识别产品线且不一致 → 排除
      4. 至少共享一个非产品线锚点词（长度 ≥2、非纯数字）
    """
    if not original:
        return []
    ot = set(tokenize(original))
    orig_line = product_line(ot)
    orig_base = os.path.basename(original)

    out, seen = [], set()
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        if category and e.get("target") != category:
            continue
        fname = e.get("filename") or ""
        if not fname or os.path.basename(fname) == orig_base:
            continue
        if fname in seen:
            continue
        et = set(tokenize(fname))
        e_line = product_line(et)
        if orig_line and e_line and e_line != orig_line:
            continue
        shared = {t for t in (ot & et) - PRODUCT_LINE_TOKENS
                  if _is_anchor_token(t)}
        if not shared:
            continue
        seen.add(fname)
        item = dict(e)
        # 参考分：共享锚点数为主，同产品线额外加分；只用于排序
        score = len(shared) + (0.5 if orig_line and e_line == orig_line else 0)
        item["ref_score"] = round(score, 2)
        out.append(item)

    out.sort(key=lambda x: (-x["ref_score"], x.get("filename", "")))
    return out[:limit]


# --------------------------------------------------------------------------
# 七、批量入口（宿主直接调这个）
# --------------------------------------------------------------------------

def classify_batch(items: list, list_provider) -> list:
    """
    批量匹配。

    :param items: [{node_id, node_type, widget, category, original}, ...]
    :param list_provider: callable(category) -> [本地相对路径]
    :return: 每条附带 status / matched / candidates / search_urls
    """
    cache = {}
    results = []
    for item in items:
        category = item.get("category") or ""
        if category not in cache:
            cache[category] = list_provider(category) if category else []
        match = match_item(item.get("original") or "", cache[category])
        results.append({**item, "category": category, **match})
    return results
