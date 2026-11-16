"""25 个可替换槽位的元数据。1:1 复刻自 ComfyArt src/customMarkers.ts 的 CUSTOM_ACTIONS。

上游声明：槽位定义移植自 jtydhr88/ComfyArt
（https://github.com/jtydhr88/ComfyArt，MIT License，Copyright (c) ComfyArt 作者）。
本项目为独立适配层，与 ComfyArt 官方无关。

每个槽位声明：
- required: 必须出现在节点 _meta.title 里的 marker（不带 @）
- optional: 可选 marker
- title_cn: 中文名（对应 ComfyArt 里的功能按钮）
- position: 在 ComfyArt 界面上的位置说明
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActionSpec:
    id: str
    group: str  # image / edit / tools / video / model / audio
    title_cn: str
    position: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    needs_images: bool = False
    needs_frames: bool = False
    note: str = ""


ACTIONS: dict[str, ActionSpec] = {
    a.id: a for a in [
        ActionSpec("generate", "image", "文生图",
                   "左侧图像生成器 / 新建画布的 generate 按钮",
                   ("prompt", "size"),
                   ("count", "unet", "image", "image2", "base", "strength", "negative"),
                   note="文生图。@image/@image2 接收参考图；@base 接收底图、@strength 是重绘幅度。"),
        ActionSpec("edit", "edit", "指令式编辑",
                   "画布上的 quick edit / edit text / compose / 样机融合",
                   ("image", "prompt"), ("image2", "image3"),
                   note="指令式编辑。@prompt 是编辑指令；@image2/@image3 是本项目扩展的多图参考位。"),
        ActionSpec("style", "edit", "风格迁移",
                   "右键图片 → 风格迁移（style transfer）",
                   ("image", "prompt"), ("image2", "image3"),
                   note="风格迁移。@image2 是风格参考图；@image3 是本项目扩展的第二张参考。"),
        ActionSpec("inpaint", "edit", "区域修复",
                   "画笔工具在图上涂抹后重绘",
                   ("image", "prompt"), needs_images=True,
                   note="区域修复。@image 收到裁剪区域，结果贴回全图。"),
        ActionSpec("expand", "edit", "扩图 Outpaint",
                   "画布边缘向外拖拽扩图",
                   ("image", "mask", "prompt"),
                   note="扩图。@image 是 pad 后的图，@mask 白色=要填的新区域。"),
        ActionSpec("angles", "edit", "多角度生成",
                   "图上右键 → 生成其他角度（multi-angle）",
                   ("image", "prompt"),
                   note="多角度。@prompt 是相机描述。"),
        ActionSpec("upscale", "tools", "放大 4x",
                   "图上右键 → Upscale → 选倍率",
                   ("image",), needs_images=True,
                   note="放大（4x-UltraSharp）。结果自动缩放到选中倍率。"),
        ActionSpec("creativeUpscale", "tools", "创意放大",
                   "图上右键 → Creative Upscale（带细节提示词）",
                   ("image", "prompt"), ("strength",), needs_images=True,
                   note="带细节增强的放大。@strength 0~1。"),
        ActionSpec("cutout", "tools", "抠图",
                   "图上右键 → Remove Background（BiRefNet）",
                   ("image",),
                   note="抠图。结果保留 alpha 通道。"),
        ActionSpec("erase", "tools", "擦除物体",
                   "橡皮工具 / 图上右键 → Erase（LaMa）",
                   ("image", "mask"),
                   note="擦除。@mask 白色=要去掉的区域。"),
        ActionSpec("layers", "tools", "图生分层",
                   "图上右键 → Split Layers（SAM3）",
                   ("image",), ("prompt", "count"),
                   note="分层。输出多张 RGBA。"),
        ActionSpec("mockup", "tools", "贴图遮罩",
                   "画布 mockup 样机贴图（本项目扩展槽位，上游 mockup-surface.api.json）",
                   ("image",),
                   note="贴图辅助。@save 输出深度图，@saveMask 输出遮罩（两个结果都返回）。"),
        ActionSpec("video", "video", "文生/图生视频",
                   "视频生成器（含首尾帧、分镜模式，MiniMax H3）",
                   ("prompt", "size"), ("image", "last", "seconds"),
                   note="视频生成。@image 是首帧、@last 是尾帧。"),
        ActionSpec("animate", "video", "让图动起来",
                   "图上右键 → Animate Image",
                   ("image", "prompt"), ("size", "seconds", "last"),
                   note="图生短视频。"),
        ActionSpec("refVideo", "video", "参考生视频 r2v",
                   "参考图/视频/音频生成视频（reference to video）",
                   ("prompt",), ("image", "image2", "video", "audio", "size", "seconds"),
                   note="参考生视频。最多两张图、一个视频、一个音频。"),
        ActionSpec("motion", "video", "运镜控制",
                   "Motion Control（角色视频+动作参考）",
                   ("image", "video", "prompt"), ("size", "seconds"),
                   note="运镜。@image 是角色，@video 是动作参考。"),
        ActionSpec("lipsync", "video", "对口型",
                   "Lipsync（人像+音频）",
                   ("image", "audio", "prompt"), ("size", "seconds"),
                   note="对口型。"),
        ActionSpec("extend", "video", "视频延长",
                   "视频右键 → Extend（接着往后生成）",
                   ("video", "prompt"), ("seconds",),
                   note="视频延长。"),
        ActionSpec("videoUpscale", "video", "视频放大",
                   "视频右键 → Upscale Video",
                   ("video",), needs_frames=True,
                   note="视频放大。"),
        ActionSpec("videoCutout", "video", "视频抠像",
                   "视频右键 → Remove Background",
                   ("video",),
                   note="视频抠像。"),
        ActionSpec("model3d", "model", "图生 3D",
                   "图上右键 → 3D → Trellis/Pixal3D",
                   ("image",),
                   note="图生 3D，输出 GLB。"),
        ActionSpec("views", "model", "多视图生 3D",
                   "3D 面板 → Multi-view to 3D",
                   ("front",), ("left", "back", "right"),
                   note="多视图生 3D。图片按 front/left/back/right 顺序传入。"),
        ActionSpec("music", "audio", "文生音乐",
                   "音频面板 → Music",
                   ("prompt",), ("lyrics", "seconds"),
                   note="文生音乐。空歌词按 [instrumental]。"),
        ActionSpec("speech", "audio", "配音 TTS",
                   "音频面板 → Voiceover（文本转语音）",
                   ("prompt",),
                   note="配音。音色由工作流自己定。"),
        ActionSpec("clone", "audio", "声音克隆",
                   "音频面板 → Voice Clone（上传参考录音）",
                   ("audio", "prompt"),
                   note="声音克隆。"),
        ActionSpec("sfx", "audio", "音效生成",
                   "音频面板 → Sound Effect",
                   ("prompt",), ("seconds",),
                   note="音效。"),
    ]
}

GROUPS = {
    "image": "图像生成",
    "edit": "图像编辑",
    "tools": "图像工具",
    "video": "视频",
    "model": "3D",
    "audio": "音频",
}
