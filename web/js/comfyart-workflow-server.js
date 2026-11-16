import { app } from "../../scripts/app.js";

// ComfyArt Workflow Server 入口按钮：
// 优先挂到左侧菜单（老版前端的 .comfy-menu）；
// 找不到菜单时用右下角悬浮按钮兜底，任何前端版本都能看到。
const URL = "http://127.0.0.1:8811/ArtWorkflow";

function makeButton(text) {
    const btn = document.createElement("button");
    btn.textContent = text;
    btn.title = "打开工作流管理界面（端口 8811）";
    btn.style.width = "100%";
    btn.style.marginTop = "8px";
    btn.onclick = () => window.open(URL, "_blank");
    return btn;
}

function tryMountMenu() {
    const menu = document.querySelector(".comfy-menu");
    if (menu) {
        menu.appendChild(makeButton("ComfyArt 工作流服务"));
        return true;
    }
    return false;
}

function mountFloating() {
    const host = document.createElement("div");
    host.style.cssText =
        "position:fixed;right:16px;bottom:16px;z-index:9999;";
    const btn = document.createElement("button");
    btn.textContent = "工作流服务";
    btn.title = "打开工作流管理界面（端口 8811）";
    btn.style.cssText =
        "background:#1f6feb;color:#fff;border:none;border-radius:8px;" +
        "padding:10px 14px;font-size:13px;cursor:pointer;" +
        "box-shadow:0 2px 8px rgba(0,0,0,.35);";
    btn.onclick = () => window.open(URL, "_blank");
    host.appendChild(btn);
    document.body.appendChild(host);
}

app.registerExtension({
    name: "ComfyArtWorkflowServer.Launcher",
    async setup() {
        // 等 DOM 就绪；菜单出现就挂菜单，超时（1.2 秒）后兜底悬浮按钮
        let mounted = false;
        const deadline = Date.now() + 1200;
        const poll = () => {
            if (mounted) return;
            if (tryMountMenu()) {
                mounted = true;
                return;
            }
            if (Date.now() > deadline) {
                mountFloating();
                mounted = true;
                return;
            }
            setTimeout(poll, 300);
        };
        poll();
    },
});
