# ComfyArt Workflow Server

> **非官方项目**：本项目与 ComfyArt 及其作者无任何关联，仅是适配 ComfyArt 工作流格式（marker 标记 + 槽位约定）的独立配置/运行层。ComfyArt 本身请访问 https://github.com/jtydhr88/ComfyArt 。

独立于 ComfyArt 画布的工作流管理后端（FastAPI）。给移动端壳用：每个运行模式（文生图、编辑、放大、视频、音乐……）都可以替换成你自己的 ComfyUI API 工作流。

规则 1:1 复刻自 ComfyArt 源码（`src/customMarkers.ts` + `src/customWorkflows.ts`，MIT License）：槽位定义、`_meta.title` 里 `@xxx` 标记解析、上传前校验、运行前自动填参数、提交本机 ComfyUI `/prompt` 并轮询结果。

## 安装与启动

前置：本机已装好 ComfyUI（建议已安装 ComfyArt 插件）。

放进 ComfyUI 的 `custom_nodes/` 目录，重启 ComfyUI 即完成安装（首次启动会自动装依赖）：

```bash
cd <你的 ComfyUI 目录>/custom_nodes
git clone https://github.com/Se7en-Cats/comfyart-workflow-server.git
```

打开管理界面：http://127.0.0.1:8811/ArtWorkflow （ComfyUI 左下角菜单里也有「ComfyArt 工作流服务」按钮）。服务随 ComfyUI 自动启动/退出，不用单独开。健康检查 `GET /health`。

### 首次使用：一键拉取内置工作流

仓库**不包含**内置工作流文件（它们来自 ComfyArt 上游）。装好后在管理界面点「检查更新」，确认后「一键更新」，即从上游拉取全部已映射的工作流到 `data/builtin/`，并自动按本机模型清单适配路径。之后每次打开会自动检查上游更新并提示，更新前自动备份原文件。国内直连 GitHub 困难时，先设置环境变量 `COMFYART_GITHUB_PROXY` 走代理再点更新。

## 在 ComfyUI 里给节点打标记

工作流必须 **Workflow → Export (API)** 导出（不是普通保存）。然后右键节点 → Title，加上 `@xxx`：

| 标记 | 打在什么节点 | 后端填什么 |
|---|---|---|
| `@image` | Load Image | 选中的图 |
| `@mask` | Load Image (mask) | 选区 mask，白=要改 |
| `@prompt` | CLIP Text Encode / String | 提示词 |
| `@negative` | 文本 widget | 负面提示词 |
| `@size` | Empty Latent Image 等 | width/height（可拆 `@width`+`@height`） |
| `@count` | Int / batch_size | 出图张数 |
| `@seconds` | seconds/duration widget | 视频/音频秒数 |
| `@strength` | Float / denoise | 重绘幅度 |
| `@video` | Load Video / VHS_LoadVideo | 视频 |
| `@audio` | Load Audio | 音频 |
| `@lyrics` | 歌词 widget | 歌词 |
| `@save` | 结果节点（多出口才需要） | 输出 |

标题大小写不敏感，可混字（`Positive @prompt`），一个节点可挂多个标记（`@prompt @size`）。

### 自动补标记

导出 API JSON 通常没有 `_meta.title`，逐个手改太慢。上传时后端默认自动按节点类型补标记（`POST /workflows/{action}` 的 `autotag` 默认为 `true`），界面也有「自动补标记」按钮；推断不出来的会在界面提示缺哪个标记。

## 扩展能力（非作者规范，可用 `COMFYART_EXTRA_MARKERS=0` 关闭）

- `@image3`：第三张图位（作者只有 `@image` / `@image2`），只在 `edit` / `style` 槽位生效，给「主图 + 2 张参考图」类工作流用。
- `@saveMask` + `mockup` 槽位：mockup 贴图工作流一次输出深度图 + 遮罩两个结果（`@save` / `@saveMask`）。
- `@unet`：运行面板的「高级：LoRA」注入点，串在 unet 加载节点之后；LoRA 须与底模同源。

## 模型依赖与一键匹配

模型依赖面板会扫描工作流里的模型加载节点，按本机清单给出状态：`ok` 不动 / `auto` 自动填入建议 / `candidate` 同基座候选（需人工核对）/ `missing` 无候选。确认后点「保存模型替换」才落盘（保存前自动备份）；真正缺失且有登记地址的显示「下载」按钮。

## API

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/actions` | 26 个槽位元数据 + 当前绑定 + 文件列表 |
| GET | `/workflows/{action}` | 某槽位下所有工作流文件 |
| GET | `/models` | 本地模型清单 |
| GET | `/workflows/{action}/models` | 扫描模型依赖，返回 ok/auto/candidate/missing |
| PUT | `/workflows/{action}/models` | 保存模型替换（先写 `.backup/`） |
| POST | `/workflows/{action}` | 保存并自动绑定；body `{name, workflow}` |
| POST | `/workflows/{action}/check` | 只校验，不保存 |
| DELETE | `/workflows/{action}/{filename}` | 删除（绑定时自动解绑） |
| PUT | `/bindings/{action}` | 切换绑定；`{filename}` 或 `{filename:null}` 回退内置 |
| POST | `/run/{action}` | 执行 |
| POST | `/uploads` | 上传图片/视频到 ComfyUI input；multipart 字段 `file` |
| GET | `/jobs/{prompt_id}` | 查任务状态/结果 |
| POST | `/interrupt` | 中断当前 |
| GET | `/art/status` | 上游更新检查状态 |
| POST | `/art/check` | 手动重新检查上游 |
| POST | `/art/update` | 一键更新内置工作流 |

`POST /run/{action}` body（所有字段可选，按槽位需要传）：

```json
{
  "image": "comfyart/xxx.png",
  "prompt": "a cat on the moon",
  "width": 1024, "height": 1024, "count": 1,
  "seconds": 5, "strength": 0.6
}
```

图片/视频/音频传 ComfyUI input 目录下的相对路径（界面输入框旁的「上传」按钮会自动经 `/uploads` 上传并回填）。返回 `{prompt_id}` 后轮询 `GET /jobs/{prompt_id}`，成功时 `files[]` 里每项是 `{kind, url}`。

## 与 ComfyArt 原生行为的差异

- 不做 Cloud/LAN 转发（直连本机 ComfyUI）
- 运行时不自动改模型名：一键匹配给出建议，`candidate` 需人工核对保存
- 不做分镜自动拆分、padForOutpaint 等图后处理（需要的话在工作流里写死）
- 上传即绑定（与 ComfyArt "install" 一致）

## 许可证

- 本项目：**PolyForm Noncommercial License 1.0.0**，见 [LICENSE](LICENSE)。个人使用、学习研究、非商业项目可自由使用与修改；**商业使用需另行授权**（要商用请联系作者）。
- `markers.py`（marker 规范/校验/填充）与 `actions.py`（槽位定义）移植自 [jtydhr88/ComfyArt](https://github.com/jtydhr88/ComfyArt)（MIT License），版权声明保留于各文件头部。
- 内置工作流文件不随本仓库分发；「一键更新」时从上游 ComfyArt 仓库（MIT License）拉取到本机 `data/builtin/`。
- `autofill.py` 算法内核来自 `model-autofill-kit`。
- 本项目与 ComfyArt 官方无任何关联。
