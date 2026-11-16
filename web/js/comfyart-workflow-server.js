// ComfyArt Workflow Server 启动入口
// 纯 DOM 轮询挂载，不依赖任何前端版本 API：
// 1) 优先挂在顶栏「ComfyArt」按钮旁边（新版前端）
// 2) 老版前端挂左下角 .comfy-menu
// 3) 都没有则右下角悬浮按钮兜底
(() => {
    const URL = "http://127.0.0.1:8811/ArtWorkflow";
    const BTN_ID = "__cwfs_launcher";

    function makeButton() {
        const btn = document.createElement("button");
        btn.id = BTN_ID;
        btn.textContent = "工作流服务";
        btn.title = "打开工作流管理界面（端口 8811）";
        btn.style.cssText =
            "display:inline-flex;align-items:center;height:32px;" +
            "padding:0 14px;margin-left:8px;border:1px solid rgba(255,255,255,.18);" +
            "border-radius:6px;background:rgba(255,255,255,.06);color:#eee;" +
            "font-size:13px;cursor:pointer;white-space:nowrap;" +
            "transition:background .15s;";
        btn.onmouseenter = () => (btn.style.background = "rgba(255,255,255,.14)");
        btn.onmouseleave = () => (btn.style.background = "rgba(255,255,255,.06)");
        btn.onclick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            window.open(URL, "_blank");
        };
        return btn;
    }

    function findTopbarComfyArt() {
        const nodes = document.querySelectorAll("button, a, [role='button'], span, div");
        for (const el of nodes) {
            // 排除我们的按钮本身，避免自匹配
            if (el.id === BTN_ID) continue;
            const text = (el.textContent || "").trim();
            if (text === "ComfyArt" && el.children.length <= 1) {
                // 取最深的匹配：文本直接在自己的文本节点上
                const ownText = Array.from(el.childNodes)
                    .filter((n) => n.nodeType === Node.TEXT_NODE)
                    .map((n) => n.textContent)
                    .join("")
                    .trim();
                if (ownText === "ComfyArt") return el;
            }
        }
        return null;
    }

    function tryMountTopbar() {
        const anchor = findTopbarComfyArt();
        if (!anchor) return false;
        if (document.getElementById(BTN_ID)) return true; // 已挂
        const btn = makeButton();
        anchor.insertAdjacentElement("afterend", btn);
        return true;
    }

    function tryMountMenu() {
        const menu = document.querySelector(".comfy-menu");
        if (!menu) return false;
        if (document.getElementById(BTN_ID)) return true;
        const btn = makeButton();
        btn.style.marginTop = "8px";
        btn.style.width = "100%";
        btn.style.display = "block";
        menu.appendChild(btn);
        return true;
    }

    function tryMountFloating() {
        if (document.getElementById(BTN_ID)) return true;
        const btn = makeButton();
        btn.style.cssText =
            "position:fixed;right:16px;bottom:16px;z-index:9999;" +
            "background:#1f6feb;color:#fff;border:none;border-radius:8px;" +
            "padding:10px 14px;font-size:13px;cursor:pointer;" +
            "box-shadow:0 2px 8px rgba(0,0,0,.35);";
        document.body.appendChild(btn);
        return true;
    }

    function tick() {
        tryMountTopbar() || tryMountMenu() || tryMountFloating();
    }

    // DOM 就绪即开始，之后每 1.5s 兜底（前端重渲染弄丢按钮时自动补挂）
    if (document.body) tick();
    else window.addEventListener("DOMContentLoaded", tick);
    setInterval(tick, 1500);
})();
