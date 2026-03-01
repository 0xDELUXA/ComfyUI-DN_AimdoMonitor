import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "AimdoMonitor",

    async setup() {
        // Check initial status
        let polling = false;
        try {
            const res = await fetch("/aimdo_monitor/status");
            const data = await res.json();
            polling = data.polling;
            if (!data.available) {
                console.warn("[aimdo_monitor] aimdo not available");
            }
        } catch (e) {
            console.error("[aimdo_monitor] Could not reach monitor API", e);
        }

        // Restore saved position or use default
        const savedX = parseInt(localStorage.getItem("aimdo_btn_x") ?? -1);
        const savedY = parseInt(localStorage.getItem("aimdo_btn_y") ?? -1);

        // Build the button
        const btn = document.createElement("button");
        btn.id = "aimdo-monitor-btn";

        const updateBtn = (isPolling) => {
            polling = isPolling;
            btn.textContent = isPolling ? "⏹ Stop VRAM Poll" : "▶ Start VRAM Poll";
            btn.style.background = isPolling
                ? "linear-gradient(135deg, #c0392b, #e74c3c)"
                : "linear-gradient(135deg, #1a1a2e, #16213e)";
            btn.style.boxShadow = isPolling
                ? "0 0 12px rgba(231,76,60,0.5)"
                : "0 0 12px rgba(100,180,255,0.2)";
        };

        Object.assign(btn.style, {
            position: "fixed",
            bottom: savedY >= 0 ? "auto" : "20px",
            right:  savedX >= 0 ? "auto" : "20px",
            top:    savedY >= 0 ? savedY + "px" : "auto",
            left:   savedX >= 0 ? savedX + "px" : "auto",
            zIndex: "9999",
            padding: "10px 18px",
            border: "1px solid rgba(255,255,255,0.15)",
            borderRadius: "8px",
            color: "#e0e0e0",
            fontFamily: "'Courier New', monospace",
            fontSize: "13px",
            fontWeight: "bold",
            letterSpacing: "0.5px",
            cursor: "grab",
            transition: "background 0.2s ease, box-shadow 0.2s ease",
            backdropFilter: "blur(6px)",
            userSelect: "none",
        });

        updateBtn(polling);

        // --- Drag logic ---
        let dragging = false;
        let dragOffsetX = 0, dragOffsetY = 0;
        let dragMoved = false;

        btn.addEventListener("mousedown", (e) => {
            dragging = true;
            dragMoved = false;
            const rect = btn.getBoundingClientRect();
            dragOffsetX = e.clientX - rect.left;
            dragOffsetY = e.clientY - rect.top;
            btn.style.cursor = "grabbing";
            btn.style.transition = "none";
            e.preventDefault();
        });

        document.addEventListener("mousemove", (e) => {
            if (!dragging) return;
            dragMoved = true;
            const x = e.clientX - dragOffsetX;
            const y = e.clientY - dragOffsetY;
            btn.style.left = x + "px";
            btn.style.top  = y + "px";
            btn.style.right  = "auto";
            btn.style.bottom = "auto";
        });

        document.addEventListener("mouseup", (e) => {
            if (!dragging) return;
            dragging = false;
            btn.style.cursor = "grab";
            btn.style.transition = "background 0.2s ease, box-shadow 0.2s ease";
            if (dragMoved) {
                // Save position so it persists across reloads
                localStorage.setItem("aimdo_btn_x", parseInt(btn.style.left));
                localStorage.setItem("aimdo_btn_y", parseInt(btn.style.top));
            }
        });

        btn.addEventListener("click", async (e) => {
            if (dragMoved) return; // don't toggle if we were dragging
            try {
                const endpoint = polling ? "/aimdo_monitor/stop" : "/aimdo_monitor/start";
                const res = await fetch(endpoint, { method: "POST" });
                const data = await res.json();
                updateBtn(data.polling);
            } catch (e) {
                console.error("[aimdo_monitor] Toggle failed", e);
            }
        });

        document.body.appendChild(btn);
    }
});
