import { app } from "../../scripts/app.js";

// 在 ComfyUI 左侧菜单底部加一个入口按钮，打开 ComfyArt Workflow Server 管理界面。
app.registerExtension({
    name: "ComfyArtWorkflowServer.Launcher",
    async setup() {
        const waitMenu = () => {
            const menu = document.querySelector(".comfy-menu");
            if (!menu) {
                setTimeout(waitMenu, 300);
                return;
            }
            const btn = document.createElement("button");
            btn.textContent = "ComfyArt 工作流服务";
            btn.title = "打开工作流管理界面（端口 8811）";
            btn.style.width = "100%";
            btn.style.marginTop = "8px";
            btn.onclick = () => {
                window.open("http://127.0.0.1:8811/ArtWorkflow", "_blank");
            };
            menu.appendChild(btn);
        };
        waitMenu();
    },
});
