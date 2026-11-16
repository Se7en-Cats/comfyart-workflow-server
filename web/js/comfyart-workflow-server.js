// ComfyArt Workflow Server 启动入口
// 纯 DOM 轮询挂载，不依赖任何前端版本 API：
// 1) 优先挂在顶栏「ComfyArt」按钮旁边（穿透 shadow DOM 查找）
// 2) 老版前端挂左下角可见的 .comfy-menu
// 3) 都没有则右下角悬浮按钮兜底
(() => {
    const URL = "http://127.0.0.1:8811/ArtWorkflow";
    const BTN_ID = "__cwfs_launcher";

    // 执行信标：脚本运行到哪里都会向 8811 打点（记入 server.log），便于排查
    function report(stage) {
        try {
            fetch(
                "http://127.0.0.1:8811/health?cwfs_js=" + encodeURIComponent(stage) + "&t=" + Date.now(),
                { mode: "no-cors" }
            ).catch(() => {});
        } catch (e) {}
    }
    report("js-loaded");

    // 诊断：上报页面顶部区域的元素结构（含 iframe / open-shadow），
    // 用于定位顶栏「ComfyArt」按钮到底在哪一层 DOM
    function diag() {
        try {
            const q = [];
            const iframes = document.querySelectorAll("iframe");
            iframes.forEach((f, i) => {
                q.push("if" + i + ":" + ((f.src || f.id || "").slice(0, 44)));
            });
            const top = [];
            const walk = (root, depth) => {
                for (const el of root.querySelectorAll("*")) {
                    const r = el.getBoundingClientRect();
                    if (r.top < 170 && r.width > 30 && r.height > 8) {
                        const name = el.tagName.toLowerCase() +
                            "#" + (el.id || "").slice(0, 12) +
                            "." + String(el.className || "").slice(0, 18);
                        top.push(
                            "d" + depth + ":" + name + ":" +
                            Math.round(r.left) + "," + Math.round(r.top) + "," +
                            Math.round(r.width) + "x" + Math.round(r.height) +
                            (el.shadowRoot ? "[o]" : "")
                        );
                        if (top.length >= 13) { q.push("T=" + top.join("|")); return; }
                    }
                    if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
                }
            };
            walk(document, 0);
            if (!q.some((s) => s.startsWith("T="))) q.push("T=" + top.join("|"));
            fetch(
                "http://127.0.0.1:8811/health?cwfs_diag=" + encodeURIComponent(q.join(" & ")),
                { mode: "no-cors" }
            ).catch(() => {});
        } catch (e) {}
    }
    // 顶栏可能晚渲染，分多轮上报
    [2500, 8000, 20000, 45000, 90000].forEach((ms) => setTimeout(diag, ms));

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

    // 穿透 open shadow DOM 找文本包含 ComfyArt 的最小元素
    function findTopbarComfyArt() {
        let best = null;
        const visit = (root) => {
            for (const el of root.querySelectorAll("*")) {
                if (el.id === BTN_ID) continue;
                const t = (el.textContent || "").trim();
                if (t === "ComfyArt" || (t.includes("ComfyArt") && t.length <= 15)) {
                    if (!best || el.textContent.length < best.textContent.length) best = el;
                }
                if (el.shadowRoot) visit(el.shadowRoot);
            }
        };
        visit(document);
        if (!best) return null;
        // 向上找最近的可点击容器（button / a / [role=button]），挂到它后面
        let host = best;
        while (host && host !== document.body && host.parentNode) {
            const tag = host.tagName.toLowerCase();
            if (tag === "button" || tag === "a" || host.getAttribute("role") === "button") break;
            host = host.parentNode;
        }
        return host && host !== document.body ? host : best;
    }

    function menuVisible(menu) {
        if (!menu || !menu.isConnected) return false;
        const style = window.getComputedStyle(menu);
        if (style.display === "none" || style.visibility === "hidden") return false;
        const rect = menu.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    }

    function findVisibleMenu() {
        const menus = document.querySelectorAll(".comfy-menu");
        for (const m of menus) if (menuVisible(m)) return m;
        return null;
    }

    function currentBtn() {
        return document.getElementById(BTN_ID);
    }

    function mountTopbar() {
        const anchor = findTopbarComfyArt();
        if (!anchor) return false;
        if (currentBtn()) return true;
        const btn = makeButton();
        anchor.insertAdjacentElement("afterend", btn);
        report("mounted-topbar");
        return true;
    }

    function mountMenu() {
        const menu = findVisibleMenu();
        if (!menu) return false;
        if (currentBtn()) return true;
        const btn = makeButton();
        btn.style.marginTop = "8px";
        btn.style.width = "100%";
        btn.style.display = "block";
        menu.appendChild(btn);
        report("mounted-menu");
        return true;
    }

    function mountFloating() {
        if (currentBtn()) return true;
        const btn = makeButton();
        btn.style.cssText =
            "position:fixed;right:16px;bottom:40px;z-index:99999;" +
            "background:#1f6feb;color:#fff;border:none;border-radius:8px;" +
            "padding:10px 14px;font-size:13px;cursor:pointer;" +
            "box-shadow:0 2px 8px rgba(0,0,0,.35);";
        document.body.appendChild(btn);
        report("mounted-floating");
        return true;
    }

    // 已挂在 menu/floating 时，一旦找到顶栏锚点就搬过去
    function upgradeToTopbar() {
        const anchor = findTopbarComfyArt();
        if (!anchor) return;
        const btn = currentBtn();
        if (!btn) return;
        if (btn.parentNode === anchor.parentNode && btn.previousElementSibling === anchor) return;
        btn.remove();
        anchor.insertAdjacentElement("afterend", btn);
        report("moved-topbar");
    }

    function tick() {
        const btn = currentBtn();
        if (!btn) {
            mountTopbar() || mountMenu() || mountFloating();
            return;
        }
        // 按钮在 menu 或 floating 里时，持续尝试搬到顶栏
        upgradeToTopbar();
    }

    if (document.body) tick();
    else window.addEventListener("DOMContentLoaded", tick);
    setInterval(tick, 1500);
})();
