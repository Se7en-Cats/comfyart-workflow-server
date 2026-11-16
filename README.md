# ComfyArt Workflow Server

> **非官方项目**：本项目与 ComfyArt 及其作者无任何关联，仅是适配 ComfyArt 工作流格式（marker 标记 + 槽位约定）的独立配置/运行层。ComfyArt 本身请访问 https://github.com/jtydhr88/ComfyArt 。

独立于 ComfyArt 画布的工作流管理后端（FastAPI）。给移动端壳用：每个运行模式（文生图、编辑、放大、视频、音乐……）都可以替换成你自己的 ComfyUI API 工作流。

规则 1:1 复刻自 ComfyArt 源码（`src/customMarkers.ts` + `src/customWorkflows.ts`，MIT License），包括：
- 26 个槽位（action）的必填/可选 marker
- `_meta.title` 里 `@xxx` 标记解析
- 上传前校验（缺标记、节点类型不对、重复标记、输出节点检查）
- 运行前自动填参数、随机化 seed
- 提交到本机 ComfyUI `/prompt`，轮询 `/history` 拿结果

## 安装与启动

前置：本机已装好 ComfyUI（并建议已安装 ComfyArt 插件，因为内置工作流用的节点大部分来自它）。

```bash
pip install -r requirements.txt
python start-server.py          # 或 uvicorn main:app --host 0.0.0.0 --port 8811
```

打开 http://127.0.0.1:8811/（别名 /ArtWorkflow）进入管理界面。

环境变量：
- `COMFY_URL`：本机 ComfyUI 地址，默认 `http://127.0.0.1:8188`（本机地址会按 8188/8189/8187/8190 自动探测，ComfyUI Desktop 重启换端口不用管）
- `COMFYART_WF_DATA`：数据目录，默认 `./data`
- `PORT`：本服务端口，默认 8811
- `COMFYART_GITHUB_PROXY`：拉取上游仓库时用的代理（可选，直连 GitHub 困难时设置）
- `COMFYART_EXTRA_MARKERS=0`：关闭本项目扩展标记（`@image3`、`@saveMask`），退回作者的 21 个标记

健康检查：`GET /health`（同时验证 ComfyUI 连通）。

### 首次使用：一键拉取内置工作流

仓库**不包含**内置工作流文件（它们来自 ComfyArt 上游仓库）。装好后在管理界面点「检查更新」，确认后「一键更新」，会从上游拉取全部已映射的工作流到 `data/builtin/`，并自动按本机模型清单适配路径。之后每次打开都会自动检查上游是否有更新并提示。

### Windows 后台运行

Windows 上可注册计划任务随登录自启（失败自动重试），实际入口是 `start-server.py`，日志写到 `data/server.log`：

```powershell
Register-ScheduledTask -TaskName ComfyArtWorkflowServer `
  -Action (New-ScheduledTaskAction -Execute "<venv>\Scripts\pythonw.exe" -Argument '"<本目录>\start-server.py"' -WorkingDirectory "<本目录>") `
  -Trigger (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME") `
  -Settings (New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1))
Start-ScheduledTask -TaskName ComfyArtWorkflowServer
```

`uvicorn` 没有空闲超时，浏览器长时间不发请求不会让它退出。

## 目录

```
data/
  workflows/<action>/<名字>.json   # 你上传的工作流（按槽位分文件夹）
  builtin/<action>.json           # 内置兜底：从 ComfyArt 仓库 workflows/*.api.json 拷进来命名为 <action>.json
  builtin/<action>/<上游名>.json   # 内置变体：同一槽位的其它上游工作流（generate/music/model3d）
  builtin/.backup/<时间戳>/       # 一键更新前的原文件备份
  workflow-config.json             # 绑定关系 {action: 文件名, variants: {action: 变体名}}
  art-update.json                  # 最近一次上游检查结果
```

### 内置变体（方案 A）

同一槽位可挂多个上游内置工作流：`generate` 有 7 个（z-image-turbo / krea2-turbo / flux2-klein / flux3-klein / moss-image / qwen-image-2512 / ideogram4），`music` 有 2 个（ace-step15 / yue2），`model3d` 有 1 个（pixal3d）。前端「当前绑定」卡片下有「内置变体」下拉，切换立即生效（模型依赖卡、输入框同步刷新）；选了自定义绑定后变体下拉禁用。接口 `PUT /bindings/{action}/variant`，body `{"variant": "xxx.api.json"}` 或 `{"variant": null}` 回到默认。

## ComfyArt 内置工作流更新

服务每次启动都会在后台检查 `jtydhr88/ComfyArt` 的 `main` 分支：

1. 读取最新 commit；
2. 用 GitHub git tree 的 blob SHA 与本地 `data/builtin/*.json` 比较，不会下载 33MB 的整仓 zip；
3. 只有 blob 不同时才拉取对应的单个 raw 文件，再做 JSON 语义比较（只改缩进不会误报）；
4. 有内容差异时，管理界面顶部出现橙色提示条，显示变更的槽位；
5. 点「一键更新」后才写入 `data/builtin`，写入前备份到 `data/builtin/.backup/<YYYYmmdd-HHMMSS>/`。

范围限制：
- 只更新 `WORKFLOW_MAP` 的 17 个默认兜底 + `VARIANT_MAP` 的 10 个内置变体；不会碰 `data/workflows/**`、`markers.py`、`actions.py`。
- 仍暂缓未纳入的 4 个上游工作流（minimax-h3-image、speech/voice-clone qwen3tts 缺本机节点；transcribe-gemma4 是上游内部转写），状态里会以 `unmapped` 返回。
- GitHub 不可达时，`GET /art/status` 的 `error` 会记录原因，界面显示「重试检查」；不影响服务启动和已有工作流运行。
- 需要走代理时设置 `COMFYART_GITHUB_PROXY=http://127.0.0.1:4780`。

### 内置工作流的本机路径适配（`local_adapt.py`）

上游 `workflows/*.api.json` 里的模型文件名是作者环境的写法（常缺子目录前缀，本机跑会报 "Value not in list"）。检查/更新落盘后，会用 ComfyUI `/object_info` 实时枚举做一次适配：

- 某条模型引用的 **basename 在枚举里存在且唯一**时，替换成枚举里的完整值（如 `minimax_h3_video_vae_fp16.safetensors` → `minimax_h3\minimax_h3_video_vae_fp16.safetensors`）；
- basename 不存在或同名多个候选时**不动**，绝不猜测——真缺文件仍交给模型依赖面板的一键匹配/下载；
- 适配前原文件备份到 `data/builtin/.backup/adapt/<时间戳>/`；
- 适配结果记录到 `data/builtin/.adapted.json`，检查更新时把「本地=上游的已适配版本」判为一致，不会反复提示更新、也不会被一键更新覆盖回未适配状态；
- `GET /art/status` 的 `adapted` 字段展示最近一次适配改了什么；ComfyUI 不可达时跳过适配（下次检查再试）。

### ComfyUI 端口自动探测

ComfyUI Desktop 每次启动可能换端口（8188/8189/8187/8190）。配置地址不通且是本机地址（127.0.0.1/localhost）时，自动按候选顺序探测并切换；远程/LAN 地址不漂移。`GET /config`、`GET /health` 展示的是实际使用的地址。

接口：
- `GET /art/status`：检查状态、变更列表、上游 commit、未映射文件。
- `POST /art/check`：手动强制重新检查。
- `POST /art/update`：一键更新已映射的内置文件。

## 在 ComfyUI 里给节点打标记（关键）

工作流必须 **Workflow → Export (API)** 导出（不是普通保存）。然后右键节点 → Title，加上 `@xxx`：

| 标记 | 打在什么节点 | 后端填什么 |
|---|---|---|
| `@image` | Load Image | 选中的图 |
| `@mask` | Load Image (mask) | 选区 mask，白=要改 |
| `@prompt` | CLIP Text Encode / String | 提示词 |
| `@negative` | 文本 widget（qwen-image-2512 等） | 负面提示词 |
| `@size` | Empty Latent Image / 带 width+height 的节点 | width/height（可拆成 `@width`+`@height`） |
| `@count` | Int 或 batch_size | 出图张数 |
| `@seconds` | seconds/duration/max_duration widget | 视频/音频秒数 |
| `@strength` | Float 或 denoise | 重绘幅度 |
| `@video` | Load Video / VHS_LoadVideo | 视频 |
| `@audio` | Load Audio | 音频 |
| `@lyrics` | 歌词 widget | 歌词 |
| `@save` | 结果节点（多出口才需要） | 输出 |

标题大小写不敏感，可以混字（`Positive @prompt`），一个节点可以挂多个标记（`@prompt @size`）。

### 自动补标记（不用手改）

ComfyUI 导出的 API JSON 通常没有 `_meta.title`，逐个手改太慢。后端内置自动推断（`autotag.py`）：

- **上传时默认自动补**：`POST /workflows/{action}` 的 `autotag` 字段默认为 `true`，保存前先按节点类型补标记，再把补好的版本落盘；返回 `autotag` 列表说明补了什么。
- **已上传文件可重标**：界面上「当前绑定」卡片的「自动补标记」按钮，或 `POST /workflows/{action}/{filename}/autotag`。原文件会先备份到 `data/workflows/<action>/.backup/`。
- **只推断不落盘**：`POST /workflows/{action}/autotag` 传 `{workflow}`，返回建议标记 + 打好标记的副本，用于预览。

推断依据：`@prompt` 沿 KSampler/BasicGuider 的 positive 条件链找文本节点（排除 negative 分支）；`@size` 找同时带数值 width/height 的节点（如 EmptyLatentImage），没有则从 width/height 输入沿引用链向下找最底层的数值节点（支持中间隔 `ComfyMathExpression` 这类计算节点），分别标 `@width`/`@height`；`@image`/`@image2`/`@image3`/`@base` 按 LoadImage 数量与槽位可选标记顺序分配；`@mask` 优先 LoadImageMask，其次带 MASK 输出且未被 `@image` 占用的 LoadImage；`@count`/`@seconds`/`@strength` 只在槽位允许且节点确有对应 widget 时才标（`@seconds` 只认 seconds/duration/max_duration，不会拿普通 Int 节点顶替）；`@unet` 找模型加载节点；`@video` 同时识别标准 `LoadVideo` 和 `VHS_LoadVideo` 系列；多出口时给保存节点标 `@save`。

推断不出来的（例如提示词接在 StringConcatenate 的 `string_a` 上、扩图只有一张 LoadImage 找不到 mask 节点）不会硬塞，接口返回 `unresolved`，界面会提示缺哪个标记、需要补哪种节点。

### 多图编辑扩展（`@image3`，非作者规范）

ComfyArt 的自定义工作流只有 `@image` / `@image2` 两个图位（`src/customMarkers.ts` 的 `Marker` 类型里没有 `image3`），而 `edit` 槽位连 `@image2` 都不开放。为了让「主图 + 2 张参考图」这类工作流（Qwen Image Edit 2509、Krea2 identity 等）能跑，本项目加了扩展图位：

- `@image3`：第三张图，只能打在 `LoadImage` 上，只在 `edit` / `style` 槽位生效；`@image2` 同时向这两个槽位开放。
- 校验、填充、自动补标记都已接入；前端输入区按 `actions.py` 的 `optional` **和当前绑定工作流实际存在的标记**生成——工作流没有 `@image2` 时，界面不会显示第二张图输入框，而是提示缺哪个标记。
- 运行时会拦截「填了图但工作流没有对应标记」的情况，返回 422 并提示先点「自动补标记」，不再静默丢弃输入。
- **与作者代码零交集**：扩展只存在于本项目的 `markers.py`（`EXTRA_MARKERS`）、`actions.py`、`main.py`（`RunBody`），不修改 ComfyArt 仓库任何文件；ComfyArt 更新覆盖 `src/`、`dist/`、`custom_nodes/` 都与本项目无关。
- **可一键退回原版**：启动时设 `COMFYART_EXTRA_MARKERS=0`，标记集合恢复为作者的 21 个，其余逻辑不变。
- **同步作者更新时只需对照一处**：打开作者仓库的 `src/customMarkers.ts`，看 `Marker` 是否新增了 `image3`。若作者新增且语义一致（第三张图），本项目自然兼容；若语义不同，只改 `markers.py` 的 `EXTRA_MARKERS` 映射即可，核心 21 个标记保持不动。

### 贴图遮罩槽位 + `@saveMask`（非作者规范）

上游 1.3.0 的 `mockup-surface.api.json`（画布 mockup 贴图的辅助工作流：输入图 → 深度图 + 遮罩两个输出）在作者前端属于 edit 的内部流程，没有独立槽位，作者 21 个 `Marker` 里也没有 `saveMask`。本项目扩展：

- 新槽位 `mockup`（图像工具组「贴图遮罩」）：`required=@image`，运行后同时返回两个输出——`@save` 的深度图（filename_prefix `comfyart/mockup-depth`）和 `@saveMask` 的遮罩图（`comfyart/mockup-mask`），任务结果里按 `node` 字段区分；
- 扩展标记 `savemask`（标题写 `@saveMask`）：只允许出现在输出节点上，只在 `mockup` 槽位生效；`check`/`fill` 已接入（fill 不向它写值，纯输出标记）；
- 已纳入 `WORKFLOW_MAP`（`mockup → mockup-surface.api.json`），随内置工作流检查/一键更新/本机路径适配一起管理；模型依赖扫描覆盖其两个预处理节点（`DepthAnythingV2Preprocessor.ckpt_name`、`LoadBackgroundRemovalModel.bg_removal_name`）；
- 开关同 `@image3`（`COMFYART_EXTRA_MARKERS=0` 时关闭）。

### 链式 LoRA（`@unet`）

- 运行面板的「高级：LoRA」只在绑定工作流含 `@unet` 标记时可用；没有该标记时面板显示不可用，接口层也会对带 `loras` 的运行请求返回 422，避免静默丢弃。
- 注入位置：新建 LoRA 节点串在 `@unet` 节点之后，原图里所有 `MODEL`（以及必要时 `CLIP`）引用改指向链尾。
- 节点类型按 `@unet` 的输出决定：有 `CLIP` 输出（如 `CheckpointLoaderSimple`）用 `LoraLoader`，同时改 model/clip；只有 `MODEL`（`UNETLoader`、`LoraLoaderModelOnly`）用 `LoraLoaderModelOnly`，否则 ComfyUI 会报 `Required input is missing`（缺必填的 `clip`）。
- `VAE` 等其他输出引用保持指向原节点，不会被 LoRA 链改写。
- LoRA 必须与底模同源（Krea2、Z-Image、FLUX、Qwen 等不能混用），选错可能报权重不匹配或效果异常。

## 模型依赖与一键匹配

模型依赖面板会扫描工作流里的模型加载节点（`CheckpointLoader*`、`UNETLoader`、`DiffusionModelLoaderKJ`、`CLIPLoader`、`CLIPVisionLoader`、`VAELoader`、`LoraLoader*`、`UpscaleModelLoader`、`DownloadAndLoadGIMMVFIModel` 等），按 `object_info` 的本地清单判断每条引用：

| 状态 | 含义 | 一键匹配行为 |
|---|---|---|
| `ok` | 文件完全一致 | 不动 |
| `auto` | 文件名完全一致，只是子目录不同 | 填入建议值 |
| `candidate` | 同基座但精度/量化/版本不同，相似度 ≥ 0.72 | 填入候选，标「同基座候选」，必须人工核对 |
| `missing` | 无本地候选 | 不填，保留原文件名 |

「一键匹配」只把 `auto` / `candidate` 的建议填进「替换为」，不会直接写 JSON；确认后点「保存模型替换」才会落盘。保存前会把原文件备份到 `data/workflows/<action>/.backup/<filename>`。算法来自 `model-autofill-kit` 的零依赖核心（`autofill.py`）：分词剔除精度/结构词，文件名完全一致才自动，相似度只产出候选，互斥变体（base/sft、fl2va/ref2va）会被压低，宁可判缺失也不乱配。

真正 `missing` 且无候选的行会在模型头部出现「下载」按钮：有登记精确地址的走模型页/直链，没有登记的落到 HuggingFace 搜索；它只负责打开入口，不会自动写工作流。已知官方等效对 `dino_v3_L_naf_fp32.safetensors` ↔ `dino_v3_vit_l.safetensors` 会显示为 `candidate`，需人工确认后保存。

精确直链登记在 `models.py` 的 `MODEL_DOWNLOADS`（键为文件名小写），目前覆盖：flux3-image-klein、dino_v3、acestep_v1.5_xl_turbo_bf16、yue2_3b_int8_convrot（官方转制）、qwen_2.5_vl_7b_fp8_scaled 与 qwen3vl_4b_int8_convrot（社区转制，官方无同款）、depth_anything_v2_vitl.pth（mockup 深度预处理器）。所有直链在登记时做过 HEAD 验证；新增缺件时按同样方式补登记即可。

## API

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/actions` | 26 个槽位元数据（作者 25 个 + 本项目 mockup）+ 当前绑定 + 文件列表 |
| GET | `/workflows/{action}` | 某槽位下所有工作流文件 |
| GET | `/models` | 本地模型清单（按类别） |
| GET | `/workflows/{action}/models` | 扫描模型依赖，返回 ok/auto/candidate/missing、候选及 `download_url/download_note` |
| PUT | `/workflows/{action}/models` | 保存模型替换；body `{replacements:[{node_id,field,value}]}`，保存前写 `.backup/` |
| POST | `/workflows/{action}/check` | 只校验，不保存；body `{workflow}` |
| POST | `/workflows/{action}` | 保存并自动绑定；body `{name, workflow}` |
| DELETE | `/workflows/{action}/{filename}` | 删除（绑定时自动解绑） |
| PUT | `/bindings/{action}` | 切换绑定；body `{filename}` 或 `{filename:null}` 回退内置 |
| POST | `/run/{action}` | 执行；body 见下 |
| POST | `/uploads` | 上传图片/视频到 ComfyUI 的 input 目录；multipart 字段名 `file`，返回 `{ok, filename}`（图片上限 64MB，视频 512MB） |
| GET | `/jobs/{prompt_id}` | 查任务状态/结果 |
| POST | `/interrupt` | 中断当前 |

`POST /run/{action}` body（所有字段可选，按槽位需要传）：
```json
{
  "image": "comfyart/xxx.png",
  "prompt": "a cat on the moon",
  "width": 1024, "height": 1024, "count": 1,
  "seconds": 5, "strength": 0.6
}
```
图片/视频/音频传 ComfyUI input 目录下的相对路径。界面上每个图片输入框和 `@video` 输入框右侧都有「上传」按钮：选本地文件 → 经本服务 `POST /uploads` 转发到 ComfyUI 的 `/upload/image` → 自动回填返回的文件名，避免手打文件名不存在导致的 `LoadImage: Custom validation failed`。

返回 `{prompt_id}`，然后轮询 `GET /jobs/{prompt_id}`：
- `queuing` / `running`：继续等
- `success`：`files[]` 里每项是 `{kind: image|video|audio|3d|text, url}`，url 指向 `COMFY_URL/view?...`
- `error`：抛错

## 与 ComfyArt 原生行为的差异

- 不做 Cloud/LAN 转发（你直连本机 ComfyUI）
- 运行时不自动改模型名：模型依赖面板提供一键匹配建议，`candidate` 需人工核对并保存；不会在 `/run` 时静默替换模型
- 不做分镜自动拆分、padForOutpaint 等图后处理：这些 ComfyArt 是在前端 graph 上动态加节点的；移动端壳需要的话在 `/run` 之后自己做，或在工作流里写死
- 上传即绑定（跟 ComfyArt "install" 一致）

## 许可证

- 本项目：MIT License，见 [LICENSE](LICENSE)。
- `markers.py`（marker 规范/校验/填充）与 `actions.py`（槽位定义）移植自 [jtydhr88/ComfyArt](https://github.com/jtydhr88/ComfyArt)（MIT License），已在文件头保留上游声明。
- 内置工作流文件不随本仓库分发；使用「一键更新」时从上游 ComfyArt 仓库（MIT License）拉取到本机 `data/builtin/`。
- `autofill.py` 算法内核来自 `model-autofill-kit`。
- 本项目与 ComfyArt 官方无任何关联。
