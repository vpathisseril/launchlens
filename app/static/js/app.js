/* LaunchLens client — charts (ECharts, theme-aware), icons, HTMX CSRF, table sort. No eval, CSP-safe. */
(function () {
  "use strict";
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const list = (n) => css(n).split(",").map((s) => s.trim()).filter(Boolean);
  const fmtNum = (v) => {
    if (v === null || v === undefined || v === "-" || isNaN(v)) return "—";
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(1) + "B";
    if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
    return Number.isInteger(v) ? String(v) : v.toFixed(1);
  };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const F = {
    "ll:pct0": (p) => (Array.isArray(p.value) ? Math.round(p.value[2]) + "%" : p.value),
    "ll:tile": (p) => `<b>${esc(p.name)}</b>: ${p.value && p.value[2] !== "-" ? Number(p.value[2]).toFixed(1) : "no data"}`,
    "ll:tilelabel": (p) => `${p.name}\n${p.value && p.value[2] !== "-" ? Math.round(p.value[2]) : "·"}`,
    "ll:point": (p) => `<b>${esc(p.data[2])}</b><br>x: ${p.data[0]}<br>y: ${p.data[1]}`,
    "ll:name": (p) => p.data[2],
    "ll:treemap": (p) => `<b>${esc(p.name)}</b><br>$${fmtNum(p.value)}` + (p.data && p.data.yoy != null ? `<br>YoY ${p.data.yoy}%` : ""),
    "ll:num1": (p) => fmtNum(p.value),
    "ll:span": (p) => `<b>${esc(p.name)}</b><br>${esc(p.seriesName)}`,
    "ll:year": (v) => String(Math.round(v)),
    "ll:radar": (p) => `<b>${esc(p.data[0])}</b> · ${esc(p.data[1])}<br>${esc(p.data[2])}`,
  };

  function resolve(obj) {
    if (Array.isArray(obj)) return obj.map(resolve);
    if (obj && typeof obj === "object") {
      const out = {};
      for (const [k, v] of Object.entries(obj)) {
        if (k === "ll_ramp") {
          out.inRange = { color: list(v === "div" ? "--div" : "--seq") };
          out.textStyle = { color: css("--muted") };
          continue;
        }
        if (k === "ll_text") {
          out.fill = css(v === "accent" ? "--accent" : "--muted");
          out.font = "600 12px Inter";
          continue;
        }
        out[k] = resolve(v);
      }
      return out;
    }
    if (typeof obj === "string" && obj.startsWith("ll:")) {
      if (obj === "ll:bg") return css("--surface");
      return F[obj] || obj;
    }
    return obj;
  }

  function themed(opt) {
    const o = resolve(opt);
    o.color = list("--chart");
    o.backgroundColor = "transparent";
    o.textStyle = { fontFamily: "Inter, sans-serif", color: css("--text") };
    const axis = (a) => {
      if (!a) return;
      (Array.isArray(a) ? a : [a]).forEach((x) => {
        x.axisLine = Object.assign({ lineStyle: { color: css("--border") } }, x.axisLine);
        x.splitLine = Object.assign({ lineStyle: { color: css("--border"), type: "dashed" } }, x.splitLine);
        x.axisLabel = Object.assign({ color: css("--muted") }, x.axisLabel);
        x.nameTextStyle = Object.assign({ color: css("--muted") }, x.nameTextStyle);
      });
    };
    axis(o.xAxis); axis(o.yAxis); axis(o.angleAxis); axis(o.radiusAxis);
    if (o.legend) o.legend.textStyle = { color: css("--muted") };
    if (o.tooltip) Object.assign(o.tooltip, { backgroundColor: css("--surface"), borderColor: css("--border"), textStyle: { color: css("--text") } });
    return o;
  }

  const charts = new Set();
  function initCharts(root) {
    if (!window.echarts) return;
    root.querySelectorAll("[data-chart]").forEach((el) => {
      if (el.dataset.ready) return;
      try {
        const opt = JSON.parse(el.dataset.chart);
        const c = echarts.init(el, null, { renderer: "canvas" });
        c.setOption(themed(opt));
        el.dataset.ready = "1";
        charts.add(c);
      } catch (e) { el.textContent = "Chart could not be rendered."; }
    });
  }
  window.addEventListener("resize", () => charts.forEach((c) => c.resize()));

  function sortable(root) {
    root.querySelectorAll("table[data-sortable] th").forEach((th, idx) => {
      if (th.dataset.bound) return;
      th.dataset.bound = "1";
      th.addEventListener("click", () => {
        const tb = th.closest("table").tBodies[0];
        const rows = Array.from(tb.rows);
        const dir = th.dataset.dir === "asc" ? "desc" : "asc";
        th.dataset.dir = dir;
        const val = (r) => {
          const t = (r.cells[idx] && r.cells[idx].innerText) || "";
          const n = parseFloat(t.replace(/[$,%★✓⚑\s]/g, "").replace(/([0-9.]+)([KMB])$/, (m, a, b) => a * { K: 1e3, M: 1e6, B: 1e9 }[b]));
          return isNaN(n) ? t.toLowerCase() : n;
        };
        rows.sort((a, b) => { const x = val(a), y = val(b); return (x > y ? 1 : x < y ? -1 : 0) * (dir === "asc" ? 1 : -1); });
        rows.forEach((r) => tb.appendChild(r));
      });
    });
  }

  function hydrate(root) {
    if (window.lucide) lucide.createIcons();
    initCharts(root);
    sortable(root);
    root.querySelectorAll("[data-scroll-bottom]").forEach((el) => { el.scrollTop = el.scrollHeight; });
  }

  document.addEventListener("DOMContentLoaded", () => hydrate(document));
  document.body.addEventListener("htmx:afterSwap", (e) => hydrate(e.target));
  document.body.addEventListener("htmx:configRequest", (e) => {
    const m = document.querySelector('meta[name="csrf-token"]');
    if (m) e.detail.headers["X-CSRF-Token"] = m.content;
  });
  document.body.addEventListener("htmx:responseError", (e) => {
    const t = e.detail.target;
    if (t) t.innerHTML = '<div class="alert err">Something went wrong loading this. Please retry.</div>';
  });
  // Close open menus on outside click
  document.addEventListener("click", (e) => {
    document.querySelectorAll("details.menu[open]").forEach((d) => { if (!d.contains(e.target)) d.removeAttribute("open"); });
  });
  // Clear the ask box after submit
  document.body.addEventListener("htmx:afterRequest", (e) => {
    const f = e.detail.elt;
    if (f && f.matches && f.matches("form[data-reset]") && e.detail.successful) f.reset();
  });
  // Live weight sum for targeting sliders
  document.addEventListener("input", (e) => {
    const form = e.target.closest && e.target.closest("form[data-weights]");
    if (!form) return;
    let s = 0;
    form.querySelectorAll("input[type=range]").forEach((r) => {
      s += Number(r.value);
      const out = form.querySelector(`[data-out="${r.name}"]`);
      if (out) out.textContent = r.value + "%";
    });
    const tot = form.querySelector("[data-total]");
    if (tot) { tot.textContent = s + "%"; tot.className = "pill " + (s === 100 ? "pill-ok" : "pill-warn"); }
  });
})();
