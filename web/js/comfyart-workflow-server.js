// ComfyArt Workflow Server 入口按钮
// 与 ComfyArt 插件同款机制：ComfyUI 官方 actionBarButtons API，按钮出现在顶栏动作栏。
// 老版前端没有动作栏时，降级挂到可见的左下角 .comfy-menu。
import { app } from "../../../scripts/app.js";

const URL = "http://127.0.0.1:8811/ArtWorkflow";
const BTN_ID = "__cwfs_launcher";

app.registerExtension({
    name: "ComfyArtWorkflowServer.ActionBar",
    actionBarButtons: [
        {
            icon: "icon-[lucide--workflow]",
            label: "工作流服务",
            tooltip: "打开工作流管理界面（端口 8811）",
            onClick: () => window.open(URL, "_blank"),
        },
    ],
});

// ---- 老版前端兜底：只挂「可见」的左下角菜单，不干扰新版 ----
function makeMenuButton() {
    const btn = document.createElement("button");
    btn.id = BTN_ID;
    btn.textContent = "ComfyArt 工作流服务";
    btn.title = "打开工作流管理界面（端口 8811）";
    btn.style.cssText =
        "display:block;width:100%;margin-top:8px;padding:8px 12px;" +
        "background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.18);" +
        "border-radius:6px;color:#eee;font-size:13px;cursor:pointer;";
    btn.onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        window.open(URL, "_blank");
    };
    return btn;
}

function menuVisible(menu) {
    if (!menu || !menu.isConnected) return false;
    const style = window.getComputedStyle(menu);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const rect = menu.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function tryMountLegacyMenu() {
    if (document.getElementById(BTN_ID)) return;
    for (const menu of document.querySelectorAll(".comfy-menu")) {
        if (menuVisible(menu)) {
            menu.appendChild(makeMenuButton());
            return;
        }
    }
}

if (document.body) tryMountLegacyMenu();
else window.addEventListener("DOMContentLoaded", tryMountLegacyMenu);
setInterval(tryMountLegacyMenu, 2000);
