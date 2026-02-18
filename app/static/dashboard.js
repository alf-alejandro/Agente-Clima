// --- Chart setup ---
const ctx = document.getElementById("capitalChart").getContext("2d");
const capitalChart = new Chart(ctx, {
    type: "line",
    data: {
        labels: [],
        datasets: [{
            label: "Capital ($)",
            data: [],
            borderColor: "#10b981",
            backgroundColor: "rgba(16,185,129,0.1)",
            fill: true,
            tension: 0.3,
            pointRadius: 2,
        }],
    },
    options: {
        responsive: true,
        scales: {
            x: {
                ticks: { color: "#64748b", maxTicksLimit: 10 },
                grid: { color: "#1e293b" },
            },
            y: {
                ticks: { color: "#64748b", callback: v => "$" + v },
                grid: { color: "#1e293b" },
            },
        },
        plugins: {
            legend: { display: false },
        },
    },
});

// --- DOM refs ---
const $id = (id) => document.getElementById(id);

function pnlColor(val) {
    return val >= 0 ? "text-emerald-400" : "text-red-400";
}

function pnlSign(val) {
    return val >= 0 ? "+" : "";
}

function esc(str) {
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function formatTime(iso) {
    if (!iso) return "-";
    const d = new Date(iso);
    return d.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
}

// --- Update UI ---
function updateUI(data) {
    // Status badge
    const running = data.bot_status === "running";
    const badge = $id("bot-status-badge");
    const dot = $id("status-dot");
    const txt = $id("status-text");
    badge.className = "flex items-center gap-2 px-3 py-1 rounded-full text-sm font-medium " +
        (running ? "bg-emerald-900/50 text-emerald-300" : "bg-red-900/50 text-red-300");
    dot.className = "w-2 h-2 rounded-full " + (running ? "bg-emerald-400 pulse-dot" : "bg-red-400");
    txt.textContent = running ? "Corriendo" : "Detenido";
    $id("btn-start").classList.toggle("hidden", running);
    $id("btn-stop").classList.toggle("hidden", !running);

    // Metrics
    $id("m-capital").textContent = "$" + data.capital_total.toFixed(2);
    $id("m-disponible").textContent = "$" + data.capital_disponible.toFixed(2);

    const pnlEl = $id("m-pnl");
    pnlEl.textContent = pnlSign(data.pnl) + "$" + data.pnl.toFixed(2);
    pnlEl.className = "text-xl font-bold mt-1 " + pnlColor(data.pnl);

    const roiEl = $id("m-roi");
    roiEl.textContent = pnlSign(data.roi) + data.roi.toFixed(2) + "%";
    roiEl.className = "text-xl font-bold mt-1 " + pnlColor(data.roi);

    $id("m-wl").textContent = data.won + " / " + data.lost + (data.stopped ? " (" + data.stopped + " SL)" : "");
    $id("m-scans").textContent = data.scan_count;

    // Sync config slider (only if user isn't actively dragging)
    const slider = $id("cfg-sl-ratio");
    if (data.stop_loss_ratio !== undefined && document.activeElement !== slider) {
        slider.value = data.stop_loss_ratio;
        $id("cfg-sl-ratio-val").textContent = data.stop_loss_ratio.toFixed(2);
    }

    // Chart
    const hist = data.capital_history || [];
    capitalChart.data.labels = hist.map(h => formatTime(h.time));
    capitalChart.data.datasets[0].data = hist.map(h => h.capital);
    capitalChart.update();

    // Open positions table
    const openTb = $id("table-open");
    const openPos = data.open_positions || [];
    if (openPos.length === 0) {
        openTb.innerHTML = "";
        $id("no-open").classList.remove("hidden");
    } else {
        $id("no-open").classList.add("hidden");
        openTb.innerHTML = openPos.map(p => `
            <tr class="border-b border-gray-800">
                <td class="q py-2 pr-3">${esc(p.question)}</td>
                <td class="num py-2 pr-3">${(p.entry_no * 100).toFixed(1)}&cent;</td>
                <td class="num py-2 pr-3">${(p.current_no * 100).toFixed(1)}&cent;</td>
                <td class="num py-2 pr-3">$${p.allocated.toFixed(2)}</td>
                <td class="num py-2 ${pnlColor(p.pnl)}">${pnlSign(p.pnl)}$${p.pnl.toFixed(2)}</td>
            </tr>
        `).join("");
    }

    // Opportunities table
    const oppsTb = $id("table-opps");
    const opps = data.last_opportunities || [];
    if (opps.length === 0) {
        oppsTb.innerHTML = "";
        $id("no-opps").classList.remove("hidden");
    } else {
        $id("no-opps").classList.add("hidden");
        oppsTb.innerHTML = opps.map(o => `
            <tr class="border-b border-gray-800">
                <td class="q py-2 pr-3">${esc(o.question)}</td>
                <td class="num py-2 pr-3">${(o.no_price * 100).toFixed(1)}&cent;</td>
                <td class="num py-2 pr-3">${(o.yes_price * 100).toFixed(1)}&cent;</td>
                <td class="num py-2 pr-3">$${o.volume.toLocaleString()}</td>
                <td class="num py-2 text-emerald-400">${o.profit_cents.toFixed(1)}&cent;</td>
            </tr>
        `).join("");
    }

    // Closed trades table
    const closedTb = $id("table-closed");
    const closed = data.closed_positions || [];
    if (closed.length === 0) {
        closedTb.innerHTML = "";
        $id("no-closed").classList.remove("hidden");
    } else {
        $id("no-closed").classList.add("hidden");
        closedTb.innerHTML = closed.map(c => {
            const statusColor = c.status === "WON" ? "text-emerald-400" :
                                c.status === "STOPPED" ? "text-yellow-400" : "text-red-400";
            return `
                <tr class="border-b border-gray-800">
                    <td class="q py-2 pr-3">${esc(c.question)}</td>
                    <td class="num py-2 pr-3">${(c.entry_no * 100).toFixed(1)}&cent;</td>
                    <td class="num py-2 pr-3">$${c.allocated.toFixed(2)}</td>
                    <td class="num py-2 pr-3 ${pnlColor(c.pnl)}">${pnlSign(c.pnl)}$${c.pnl.toFixed(2)}</td>
                    <td class="num py-2 pr-3 font-semibold ${statusColor}">${c.status}</td>
                    <td class="res py-2 pr-3">${esc(c.resolution || "-")}</td>
                    <td class="num py-2">${formatTime(c.close_time)}</td>
                </tr>
            `;
        }).join("");
    }
}

// --- Polling ---
async function fetchStatus() {
    try {
        const res = await fetch("/api/status");
        if (res.ok) {
            const data = await res.json();
            updateUI(data);
        }
    } catch (e) {
        console.error("Fetch error:", e);
    }
}

async function saveConfig() {
    const ratio = parseFloat($id("cfg-sl-ratio").value);
    const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stop_loss_ratio: ratio }),
    });
    if (res.ok) {
        const saved = $id("cfg-saved");
        saved.classList.remove("hidden");
        setTimeout(() => saved.classList.add("hidden"), 2000);
    }
}

async function startBot() {
    await fetch("/api/bot/start", { method: "POST" });
    fetchStatus();
}

async function stopBot() {
    await fetch("/api/bot/stop", { method: "POST" });
    fetchStatus();
}

// Initial fetch + interval
fetchStatus();
setInterval(fetchStatus, 5000);
