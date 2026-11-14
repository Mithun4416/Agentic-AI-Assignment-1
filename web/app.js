/* Statement-to-Insights frontend. No framework: fetch + DOM. All model output is escaped before rendering. */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const nfmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
  const inr = (n) => "₹" + nfmt.format(Math.round(Number(n) || 0));
  const monthLabel = (ym) => {
    const [y, m] = ym.split("-").map(Number);
    return new Date(y, m - 1, 1).toLocaleString("en-GB", { month: "short", year: "numeric" });
  };

  const SUGGESTED = [
    ["Analysis", "How much did I spend on food in the latest month compared with the month before?"],
    ["Lookup", "What was that big shopping payment?"],
    ["Advice", "Where am I overspending and what should I do about it?"],
    ["Follow-up", "And what about subscriptions?"],
  ];
  const VIEWS = ["home", "dashboard", "chat", "about"];

  let state = null;       // /api/state payload
  let dash = null;        // /api/dashboard payload
  let txns = [];
  let period = "";
  let trendCat = "";
  let pendingFile = null;
  let asking = false;

  // ------------------------------------------------------------------ API
  async function api(path, opts = {}) {
    const init = { credentials: "same-origin", ...opts };
    if (opts.json !== undefined) {
      init.method = opts.method || "POST";
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(opts.json);
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch {
      throw new Error("Cannot reach the server. Is `python server.py` still running?");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json();
  }

  // ------------------------------------------------------------------ feedback
  // ------------------------------------------------------------------ motion helpers
  const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const afterPaint = (fn) => requestAnimationFrame(() => requestAnimationFrame(fn));  // style flush, then animate
  const easeOutQuart = (x) => 1 - Math.pow(1 - x, 4);

  // Show / hide with a CSS transition instead of an instant pop.
  function showSoft(el) {
    clearTimeout(el._hideTimer);
    el.hidden = false;
    afterPaint(() => el.classList.add("show"));
  }
  function hideSoft(el, ms = 460) {
    el.classList.remove("show");
    clearTimeout(el._hideTimer);
    el._hideTimer = setTimeout(() => (el.hidden = true), reduceMotion() ? 0 : ms);
  }
  // Cross-fade a text change.
  function swapText(el, text) {
    if (reduceMotion()) { el.textContent = text; return; }
    el.classList.add("swap");
    setTimeout(() => { el.textContent = text; el.classList.remove("swap"); }, 230);
  }
  // Count a number up (or down) to its new value.
  function countTo(el, to, fmt, from = 0, ms = 1000) {
    cancelAnimationFrame(el._raf);
    if (reduceMotion() || to == null || from == null || from === to) { el.textContent = to == null ? "—" : fmt(to); return; }
    const t0 = performance.now();
    const tick = (now) => {
      const p = Math.min(1, (now - t0) / ms);
      el.textContent = fmt(from + (to - from) * easeOutQuart(p));
      if (p < 1) el._raf = requestAnimationFrame(tick);
    };
    el._raf = requestAnimationFrame(tick);
  }
  // Run fn once, the first time el is actually on screen (views start hidden, so this is when
  // the user can see the animation, not when the data happened to arrive).
  const visibleOnce = new IntersectionObserver((entries) => entries.forEach((e) => {
    if (!e.isIntersecting) return;
    visibleOnce.unobserve(e.target);
    const fn = e.target._onVisible; e.target._onVisible = null;
    fn && fn();
  }), { threshold: 0.15 });
  function whenVisible(el, fn) {
    el._onVisible = fn;
    visibleOnce.unobserve(el);
    visibleOnce.observe(el);
  }
  // Fade-and-rise elements as they scroll into view, with a small stagger.
  const revealer = new IntersectionObserver((entries) => entries.forEach((e) => {
    if (!e.isIntersecting) return;
    const el = e.target;
    revealer.unobserve(el);
    el.classList.add("in");
    // Hand the element back to its own transitions (hover lift etc.) once the fade is done.
    const done = () => { el.classList.remove("reveal", "in"); el.style.removeProperty("--i"); };
    const delay = 700 + (parseInt(el.style.getPropertyValue("--i"), 10) || 0) * 70 + 120;
    setTimeout(done, reduceMotion() ? 0 : delay);
  }), { rootMargin: "0px 0px -6% 0px", threshold: 0.06 });
  function reveal(nodes, { stagger = true } = {}) {
    [...nodes].forEach((n, i) => {
      n.classList.add("reveal");
      n.style.setProperty("--i", stagger ? Math.min(i, 6) : 0);
      revealer.observe(n);
    });
  }

  // ------------------------------------------------------------------ tactile controls
  // Sliding "glider" pill for tab groups. It stretches toward the destination first (leading edge
  // races ahead), then the trailing edge catches up with a small springy overshoot.
  function glide(container, target, { animate = true } = {}) {
    if (!container) return;
    let g = container.querySelector(":scope > .glider");
    if (!g) {
      g = document.createElement("span");
      g.className = "glider";
      g.setAttribute("aria-hidden", "true");
      container.prepend(g);
      animate = false;
    }
    if (!target) { g.style.opacity = "0"; g._pos = null; return; }
    if (container.offsetParent === null) return;            // hidden view: position it when shown
    const to = { x: target.offsetLeft, y: target.offsetTop, w: target.offsetWidth, h: target.offsetHeight };
    const from = g._pos;
    if (from && from.x === to.x && from.y === to.y && from.w === to.w && from.h === to.h) return;
    g._pos = to;
    g.style.opacity = "1";
    const css = (p, w = p.w) => ({ transform: `translate3d(${p.x}px, ${p.y}px, 0)`, width: `${w}px`, height: `${p.h}px` });
    g.getAnimations().forEach((a) => a.cancel());
    Object.assign(g.style, css(to));
    if (!animate || !from || from.y !== to.y || reduceMotion()) return;
    // Mid-flight the leading edge is ~70% of the way there and the trailing edge ~22%: a clear
    // stretch, without blanketing every label in between.
    const dxL = to.x - from.x, dxR = (to.x + to.w) - (from.x + from.w);
    const lead = 0.7, trail = 0.22;
    const left = from.x + dxL * (dxL > 0 ? trail : lead);
    const rightEdge = from.x + from.w + dxR * (dxR > 0 ? lead : trail);
    const mid = { x: left, y: to.y, w: rightEdge - left, h: to.h };
    const far = Math.abs(dxL) > to.w * 1.6;   // long jumps get a touch more time
    g.animate([
      { ...css(from), easing: "cubic-bezier(.5, 0, .5, 1)" },
      { ...css(mid), offset: 0.4, easing: "cubic-bezier(.2, 1.35, .4, 1)" },
      css(to),
    ], { duration: far ? 600 : 520 });
  }
  function reglideAll() {
    glide($(".tabs"), $(".tab[aria-current='page']"), { animate: false });
    const seg = $("#periodSeg");
    if (seg.children.length) glide(seg, $("button[aria-pressed='true']", seg), { animate: false });
    $$(".trace[open] .trace-tabs").forEach((t) => glide(t, $("button[aria-selected='true']", t), { animate: false }));
  }

  // Ripple, burst and ring effects on every pressable control.
  const PRESSABLE = ".btn, .tab, .chip, .seg button, .trace-tabs button, .fold > summary, .trace > summary";
  const fxLayer = document.createElement("div");
  fxLayer.className = "fx-layer";
  fxLayer.setAttribute("aria-hidden", "true");
  document.body.appendChild(fxLayer);

  function ripple(el, x, y) {
    const r = el.getBoundingClientRect();
    const size = Math.hypot(Math.max(x - r.left, r.right - x), Math.max(y - r.top, r.bottom - y)) * 2;
    const s = document.createElement("span");
    s.className = "ripple";
    Object.assign(s.style, { width: `${size}px`, height: `${size}px`, left: `${x - r.left}px`, top: `${y - r.top}px` });
    el.appendChild(s);
    s.animate([
      { transform: "translate(-50%, -50%) scale(0)", opacity: 1 },
      { transform: "translate(-50%, -50%) scale(1)", opacity: 1, offset: 0.55 },
      { transform: "translate(-50%, -50%) scale(1.06)", opacity: 0 },
    ], { duration: 720, easing: "cubic-bezier(.2, .7, .3, 1)" }).onfinish = () => s.remove();
  }
  function burst(x, y, colors, count = 14, spread = 1) {
    for (let i = 0; i < count; i++) {
      const b = document.createElement("i");
      b.className = "fx-bit";
      const size = 6 + Math.random() * 6;
      Object.assign(b.style, {
        width: `${size}px`, height: `${size}px`, background: colors[i % colors.length],
        left: `${x}px`, top: `${y}px`, borderRadius: Math.random() < 0.3 ? "50%" : "1.5px",
      });
      fxLayer.appendChild(b);
      const ang = (i / count) * Math.PI * 2 + Math.random() * 0.6;
      const dist = (42 + Math.random() * 62) * spread;
      const dx = Math.cos(ang) * dist, dy = Math.sin(ang) * dist;
      const rot = (Math.random() - 0.5) * 600;
      // phase 1: shoot out and decelerate (full size, full colour)
      // phase 2: drift a little further and settle
      // phase 3: fall slightly under "gravity" while shrinking and fading
      b.animate([
        { transform: "translate(-50%, -50%) translate(0, 0) rotate(0deg) scale(.4)", opacity: 1,
          easing: "cubic-bezier(.12, .9, .25, 1)" },
        { transform: `translate(-50%, -50%) translate(${dx * 0.85}px, ${dy * 0.85}px) rotate(${rot * 0.6}deg) scale(1.15)`, opacity: 1,
          offset: 0.38, easing: "ease-out" },
        { transform: `translate(-50%, -50%) translate(${dx}px, ${dy + 6}px) rotate(${rot * 0.8}deg) scale(1)`, opacity: 1,
          offset: 0.62, easing: "cubic-bezier(.5, 0, .9, .6)" },
        { transform: `translate(-50%, -50%) translate(${dx * 1.04}px, ${dy + 30}px) rotate(${rot}deg) scale(0)`, opacity: 0 },
      ], { duration: 820 + Math.random() * 260 }).onfinish = () => b.remove();
    }
  }
  function ring(x, y, color, size = 96) {
    const r = document.createElement("i");
    r.className = "fx-ring";
    Object.assign(r.style, { width: `${size}px`, height: `${size}px`, left: `${x}px`, top: `${y}px`, borderColor: color });
    fxLayer.appendChild(r);
    r.animate([
      { transform: "translate(-50%, -50%) scale(.12)", opacity: 1, borderWidth: "4px" },
      { transform: "translate(-50%, -50%) scale(1)", opacity: 0, borderWidth: "1.5px" },
    ], { duration: 600, easing: "cubic-bezier(.2, .8, .3, 1)" }).onfinish = () => r.remove();
  }

  // Saturated palette colours only: they read on the cream pages and on the dark hero alike.
  const BURST_RED = ["#FF2800", "#9C6644", "#FF2800", "#7F5539", "#B08968", "#D62100"];
  const BURST_WARM = ["#9C6644", "#7F5539", "#B08968", "#9C6644"];
  document.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || reduceMotion()) return;
    const el = e.target.closest(PRESSABLE);
    if (el && !el.disabled) ripple(el, e.clientX, e.clientY);
  }, { passive: true });
  // Capture phase: runs before handlers that may re-render or remove the clicked element.
  document.addEventListener("click", (e) => {
    if (reduceMotion()) return;
    const el = e.target.closest(PRESSABLE);
    if (!el || el.disabled) return;
    const r = el.getBoundingClientRect();
    let x = e.clientX, y = e.clientY;
    if (e.detail === 0) {                       // keyboard activation: effects from the centre
      x = r.left + r.width / 2; y = r.top + r.height / 2;
      ripple(el, x, y);
    }
    if (el.matches(".btn-red")) {
      ring(x, y, "rgba(255, 40, 0, .95)", 150);
      ring(x, y, "rgba(156, 102, 68, .85)", 90);    // inner echo ring
      burst(x, y, BURST_RED, 22, 1.15);
    } else if (el.matches(".chip")) {
      ring(x, y, "rgba(156, 102, 68, .7)", 90);
      burst(x, y, [...BURST_WARM, "#FF2800"], 12, 0.85);
    } else if (el.matches(".btn")) {
      ring(x, y, el.matches(".btn-dark") ? "rgba(127, 85, 57, .85)" : "rgba(156, 102, 68, .65)", 90);
      burst(x, y, BURST_WARM, 9, 0.7);
    }
  }, true);

  let toastTimer;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    showSoft(t);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => hideSoft(t), 6500);
  }

  let busyTimer;
  function busy(title, steps = []) {
    $("#busyTitle").textContent = title;
    let i = 0;
    $("#busyStep").textContent = steps[0] || "";
    clearInterval(busyTimer);
    if (steps.length > 1) busyTimer = setInterval(() => swapText($("#busyStep"), steps[++i % steps.length]), 1900);
    showSoft($("#busy"));
  }
  function idle() {
    clearInterval(busyTimer);
    hideSoft($("#busy"), 340);
  }

  const tip = $("#tip");
  function showTip(html, x, y) {
    tip.innerHTML = html;
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    tip.style.left = Math.max(8, Math.min(x + 14, innerWidth - r.width - 8)) + "px";
    tip.style.top = Math.max(8, Math.min(y + 14, innerHeight - r.height - 8)) + "px";
  }
  function bindTip(node, html) {
    node.addEventListener("mousemove", (e) => showTip(html, e.clientX, e.clientY));
    node.addEventListener("mouseleave", () => (tip.hidden = true));
    node.addEventListener("focus", () => { const r = node.getBoundingClientRect(); showTip(html, r.left + r.width / 2, r.top); });
    node.addEventListener("blur", () => (tip.hidden = true));
  }

  // ------------------------------------------------------------------ routing
  function go(view) {
    if (!VIEWS.includes(view)) view = "home";
    document.body.dataset.view = view;
    VIEWS.forEach((v) => ($("#view-" + v).hidden = v !== view));
    $$("[data-nav]").forEach((a) => {
      if (a.classList.contains("tab")) a.toggleAttribute("aria-current", a.dataset.nav === view);
      if (a.dataset.nav === view && a.classList.contains("tab")) a.setAttribute("aria-current", "page");
    });
    glide($(".tabs"), $(".tab[aria-current='page']"));
    if (view === "dashboard") afterPaint(() => {
      const seg = $("#periodSeg");
      if (seg.children.length) glide(seg, $("button[aria-pressed='true']", seg), { animate: false });
    });
    if (location.hash.slice(1) !== view) history.replaceState(null, "", "#" + view);
    window.scrollTo({ top: 0, behavior: "instant" });    // not the page's smooth scroll: we're changing pages
    const el = $("#view-" + view);
    if (!reduceMotion()) { el.classList.remove("entering"); void el.offsetWidth; el.classList.add("entering"); }
    onScroll();
    updateCta();
    if (view === "home") flow.start();
    if (view === "dashboard" && state?.statement && !dash) loadDashboard();
    if (view === "chat") $("#question").focus({ preventScroll: true });
  }
  // The red nav button always offers the next useful step.
  function ctaTarget() {
    if (!state?.statement) return ["Use sample", "sample"];
    return document.body.dataset.view === "chat" ? ["Open dashboard", "dashboard"] : ["Ask a question", "chat"];
  }
  function updateCta() {
    $("#navSample").textContent = ctaTarget()[0];
  }
  function onScroll() {
    $("#nav").classList.toggle("solid", document.body.dataset.view === "home" && scrollY > innerHeight * 0.72);
  }

  // ------------------------------------------------------------------ hero flow field (copper filaments)
  // Streamlines are traced ONCE into Path2D objects and painted onto an offscreen "base" layer.
  // Each frame only draws that cached layer plus light pulses gliding along ~70 strands (an animated
  // lineDashOffset), so the per-frame cost is a few dozen stroke calls instead of tens of thousands of
  // field evaluations. Time-based motion keeps the speed identical at 60, 120 or 144 Hz.
  const flow = (() => {
    const c = $("#flow");
    const ctx = c.getContext("2d", { alpha: false });
    const base = document.createElement("canvas");
    const bctx = base.getContext("2d", { alpha: false });
    const reduceMq = matchMedia("(prefers-reduced-motion: reduce)");
    const colors = ["#DDB892", "#B08968", "#E6CCB2", "#9C6644", "#DDB892", "#B08968"];
    const STEP = 6, MARGIN = 28;             // MARGIN: room for the parallax drift without showing edges
    let w = 0, h = 0, dpr = 1, strands = [], pulses = [];
    let raf = 0, last = 0, t = 0, onScreen = true;
    let mx = 0, my = 0, px = 0, py = 0;    // pointer target and eased parallax (-1..1)

    const field = (x, y) => {
      const k = 0.0015;
      return Math.PI * 0.4 + 1.05 * (Math.sin(x * k * 1.3) + Math.cos(y * k * 1.9) + Math.sin((x + y) * k * 0.7));
    };
    const rand = (a, b) => a + Math.random() * (b - a);

    function trace(x, y, steps) {
      const p = new Path2D();
      p.moveTo(x, y);
      let len = 0;
      for (let i = 0; i < steps; i++) {
        const a = field(x, y);
        x += Math.cos(a) * STEP; y += Math.sin(a) * STEP;
        p.lineTo(x, y);
        len += STEP;
        if (x < -60 || x > w + 60 || y < -60 || y > h + 60) break;
      }
      return { path: p, len };
    }

    function build() {
      dpr = Math.min(devicePixelRatio || 1, 1.75);
      w = c.clientWidth; h = c.clientHeight;
      if (!w || !h) return false;
      c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
      const bw = w + MARGIN * 2, bh = h + MARGIN * 2;
      base.width = Math.round(bw * dpr); base.height = Math.round(bh * dpr);

      const n = Math.round(Math.max(120, Math.min(420, (w * h) / 3000)));
      strands = Array.from({ length: n }, (_, i) => {
        const bright = i % 8 === 0;
        const { path, len } = trace(rand(-0.1, 1.1) * bw, rand(-0.25, 0.95) * bh, (rand(60, 230)) | 0);
        return { path, len, c: colors[i % colors.length], bright,
                 a: bright ? rand(0.28, 0.42) : rand(0.05, 0.17), lw: bright ? rand(1.1, 1.8) : rand(0.5, 1) };
      });

      // Cached base layer: background + every strand, faint.
      bctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      bctx.globalCompositeOperation = "source-over";
      bctx.globalAlpha = 1;
      bctx.fillStyle = "#22150D";
      bctx.fillRect(0, 0, bw, bh);
      bctx.globalCompositeOperation = "lighter";
      bctx.lineCap = "round";
      for (const s of strands) {
        bctx.globalAlpha = s.a; bctx.lineWidth = s.lw; bctx.strokeStyle = s.c;
        bctx.stroke(s.path);
      }

      // Light pulses travel along the longer strands, mostly the bright ones.
      pulses = strands.filter((s) => s.len > 300 && (s.bright || Math.random() < 0.18)).slice(0, 72).map((s) => ({
        s, dash: rand(70, 190), speed: rand(55, 150), phase: rand(0, s.len + 200),
        a: s.bright ? rand(0.55, 0.85) : rand(0.3, 0.5), lw: s.lw + rand(0.3, 0.8),
      }));
      return true;
    }

    function draw(dt) {
      t += dt / 1000;
      const ease = 1 - Math.pow(0.0025, dt / 1000);        // frame-rate-independent easing
      px += (mx - px) * ease; py += (my - py) * ease;
      const ox = -MARGIN + px * MARGIN * 0.7, oy = -MARGIN + py * MARGIN * 0.5;

      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.globalCompositeOperation = "source-over";
      ctx.globalAlpha = 1;
      ctx.drawImage(base, Math.round(ox * dpr), Math.round(oy * dpr));

      ctx.setTransform(dpr, 0, 0, dpr, ox * dpr, oy * dpr);
      ctx.globalCompositeOperation = "lighter";
      ctx.lineCap = "round";
      for (const p of pulses) {
        const period = p.dash + p.s.len;
        ctx.setLineDash([p.dash, p.s.len]);
        ctx.lineDashOffset = -((p.phase + t * p.speed) % period);
        ctx.strokeStyle = p.s.c;
        ctx.globalAlpha = p.a * 0.16; ctx.lineWidth = p.lw * 4.5;   // soft halo
        ctx.stroke(p.s.path);
        ctx.globalAlpha = p.a; ctx.lineWidth = p.lw;               // bright core
        ctx.stroke(p.s.path);
      }
      ctx.setLineDash([]);
    }

    const active = () => document.body.dataset.view === "home" && !document.hidden && onScreen && !reduceMq.matches;
    function frame(now) {
      if (!active()) { raf = 0; last = 0; return; }
      const dt = last ? Math.min(now - last, 64) : 16.7;   // clamp so a background tab never "jumps"
      last = now;
      draw(dt);
      raf = requestAnimationFrame(frame);
    }
    function start() {
      if (!w && !build()) return;
      if (!raf) draw(0);
      c.classList.add("ready");
      if (active() && !raf) raf = requestAnimationFrame(frame);
    }

    addEventListener("pointermove", (e) => {
      mx = (e.clientX / innerWidth) * 2 - 1;
      my = (e.clientY / innerHeight) * 2 - 1;
    }, { passive: true });
    new IntersectionObserver(([e]) => { onScreen = e.isIntersecting; start(); }).observe(c);
    let rt;
    addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if (build()) start(); }, 150); });
    document.addEventListener("visibilitychange", start);
    reduceMq.addEventListener?.("change", start);
    return { start };
  })();

  // ------------------------------------------------------------------ overview: model + upload
  function renderState() {
    const { llm, statement } = state;
    const line = $("#llmLine");
    line.classList.toggle("bad", !llm.ok);
    line.innerHTML = llm.ok
      ? `Model connected: <b>${esc(llm.provider)}</b> · <b>${esc(llm.model)}</b>`
      : `Model not available: ${esc(llm.error)} The dashboard still works with rule-based categories; chat needs a model.`;
    $("#footLlm").textContent = llm.ok ? `${llm.provider} · ${llm.model}` : "no model connected";

    const sel = $("#provider");
    sel.innerHTML = state.providers.map((p) => `<option${p === llm.provider ? " selected" : ""}>${esc(p)}</option>`).join("");
    if (document.activeElement !== $("#model")) $("#model").value = llm.model;

    updateCta();
    $("#dashEmpty").hidden = !!statement;
    $("#dashBody").hidden = !statement;
    if (statement) {
      $("#dashSub").textContent = `${statement.file_name} · ${statement.rows} transactions · ${statement.date_min} to ${statement.date_max}`;
      renderParseReport();
    }
    $("#chatSub").textContent = !statement
      ? "Load a statement first, then ask anything about it."
      : llm.ok
        ? "Every answer shows its trace: intent, prompts, tool calls, retrieval and the fact check."
        : "Chat needs a model. Add your API key and model to .env, or pick another provider on the overview.";
    renderChips();
  }

  function setFile(f) {
    pendingFile = f || null;
    $("#dropSub").textContent = f ? `${f.name} · ${(f.size / 1024).toFixed(0)} KB` : "or click to choose a file · up to 20 MB";
    $("#pdfPass").hidden = !(f && /\.pdf$/i.test(f.name));
    $("#btnUpload").disabled = !f;
  }

  async function loadStatement(kind) {
    busy("Reading your statement", [
      "Parser Agent: finding the table and mapping columns",
      "Categorizer Agent: rules first, then the model for unknown merchants",
      "Embedding transactions locally into ChromaDB",
    ]);
    try {
      if (kind === "sample") {
        state = await api("/api/statement/sample", { method: "POST" });
      } else {
        const fd = new FormData();
        fd.append("file", pendingFile);
        fd.append("password", $("#password").value || "");
        state = await api("/api/statement/upload", { method: "POST", body: fd });
      }
      dash = null; period = ""; trendCat = "";
      $("#thread").innerHTML = "";
      renderState();
      await Promise.all([loadDashboard(), loadTransactions()]);
      idle();
      go("dashboard");
      const st = state.statement;
      if (st.cat_stats?.llm_error) toast("The model could not categorise unknown merchants, so rules and fallbacks were used.");
      else if (st.store_error) toast(st.store_error);
    } catch (e) {
      idle();
      toast(e.message);
    }
  }

  // ------------------------------------------------------------------ dashboard
  async function loadDashboard() {
    dash = await api("/api/dashboard?period=" + encodeURIComponent(period));
    renderDashboard();
  }
  async function loadTransactions() {
    txns = (await api("/api/transactions")).transactions;
    renderTransactions();
  }

  function renderDashboard() {
    const { metrics: m, breakdown, trend, flags, months } = dash;

    // Period buttons are only rebuilt when the months change, so the glider can slide between them.
    const seg = $("#periodSeg");
    const opts = [["", "All months"], ...months.map((x) => [x, monthLabel(x)])];
    const key = opts.map((o) => o[0]).join("|");
    if (seg.dataset.key !== key) {
      seg.dataset.key = key;
      seg.innerHTML = opts.map(([v, l]) =>
        `<button type="button" data-period="${esc(v)}" aria-pressed="false"><i class="dot"></i>${esc(l)}</button>`).join("");
    }
    $$("button[data-period]", seg).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.period === period)));
    glide(seg, $("button[aria-pressed='true']", seg));

    // tiles: numbers count from their previous values (or from zero the first time)
    const rate = m.savings_rate_pct;
    const pct = (v) => v.toFixed(1) + "%";
    const tiles = [
      ["Income", m.income, inr, m.other_credits ? `plus ${inr(m.other_credits)} other credits` : "salary and interest", ""],
      ["Spend", m.expense, inr, m.invested ? `${inr(m.invested)} invested separately` : "excluding investments", ""],
      ["Left over", m.net_after_expense, inr, "income minus spend", ""],
      ["Savings rate", rate, pct, rate == null ? "no income in this period" : rate >= 20 ? "meets the 20% guideline" : "below the 20% guideline", "dark"],
    ];
    const tileBox = $("#tiles");
    const firstTiles = !tileBox.children.length;
    tileBox.innerHTML = tiles.map(([label, value, fmt, sub, cls]) =>
      `<div class="tile ${cls}"><div class="tile-label">${label}</div><div class="tile-value" title="${esc(value == null ? "—" : fmt(value))}">${
        firstTiles ? fmt(0) : esc(tileBox._last?.[label] == null ? "—" : fmt(tileBox._last[label]))}</div><div class="tile-sub">${esc(sub)}</div></div>`).join("");
    const prev = tileBox._last || {};
    tileBox._last = Object.fromEntries(tiles.map(([label, value]) => [label, value]));
    const runCount = () => $$(".tile-value", tileBox).forEach((el, i) => {
      const [label, value, fmt] = tiles[i];
      countTo(el, value, fmt, firstTiles ? 0 : prev[label] ?? 0);
    });
    if (firstTiles) { reveal(tileBox.children); whenVisible(tileBox, runCount); } else runCount();

    // categories: sorted horizontal bars, one hue (magnitude, not identity).
    // Each bar starts at its previous width (or 0) and glides to the new one.
    const cats = breakdown.categories || [];
    const box = $("#catBars");
    const prevW = box._w || {};
    box._w = {};
    box.innerHTML = "";
    $("#catNote").textContent = cats.length ? `${inr(breakdown.total_spend)} total` : "";
    if (!cats.length) box.innerHTML = `<p class="chart-empty">No spending in this period.</p>`;
    const max = Math.max(...cats.map((c) => c.amount), 1);
    cats.forEach((c, i) => {
      const target = (c.amount / max * 100).toFixed(2) + "%";
      box._w[c.category] = target;
      const row = document.createElement("div");
      row.className = "hbar";
      row.tabIndex = 0;
      row.setAttribute("role", "img");
      row.setAttribute("aria-label", `${c.category}: ${inr(c.amount)}, ${c.share_pct}% of spend, ${c.count} transactions`);
      row.innerHTML = `<span class="hbar-name">${esc(c.category)}</span>
        <span class="hbar-track"><span class="hbar-fill" data-w="${target}" style="width:${prevW[c.category] || "0%"};--i:${i}"></span></span>
        <span class="hbar-val">${inr(c.amount)}<span>${c.share_pct.toFixed(1)}%</span></span>`;
      bindTip(row, `<b>${esc(c.category)}</b><div class="tip-row"><span>Amount</span><span>${inr(c.amount)}</span></div>
        <div class="tip-row"><span>Share of spend</span><span>${c.share_pct.toFixed(1)}%</span></div>
        <div class="tip-row"><span>Transactions</span><span>${c.count}</span></div>`);
      box.appendChild(row);
    });
    growWhenVisible(box);

    // monthly trend
    const allCats = [...new Set(trend.flatMap((t) => Object.keys(t.categories)))].sort();
    if (trendCat && !allCats.includes(trendCat)) trendCat = "";
    $("#trendCat").innerHTML = `<option value="">All spending</option>` +
      allCats.map((c) => `<option${c === trendCat ? " selected" : ""}>${esc(c)}</option>`).join("");
    renderTrend();

    const ordered = [...flags].sort((a, b) => (a.severity === "warning" ? 0 : 1) - (b.severity === "warning" ? 0 : 1));
    $("#alerts").innerHTML = ordered.map((f) => `
      <article class="alert ${f.severity === "warning" ? "warning" : "info"}">
        <span class="alert-tag"><i class="dot"></i>${f.severity === "warning" ? "Alert" : "Note"}</span>
        <div><h4>${esc(f.title)}</h4><p>${esc(f.detail)}</p></div>
      </article>`).join("") || `<p class="muted">Nothing unusual found.</p>`;
    reveal($$("#alerts > .alert"));

    renderBudgets();
  }

  // Bars are rendered at their previous size (data-w / data-h hold the target); once the chart is on
  // screen we flush styles and set the targets, so CSS transitions glide them into place.
  function growWhenVisible(box) {
    const grow = () => afterPaint(() => {
      $$("[data-w]", box).forEach((f) => (f.style.width = f.dataset.w));
      $$("[data-h]", box).forEach((f) => (f.style.height = f.dataset.h));
    });
    if (box.offsetParent !== null && box.getBoundingClientRect().top < innerHeight) grow();
    else whenVisible(box, grow);
  }

  function renderTrend() {
    const box = $("#trendBars");
    const vals = dash.trend.map((t) => (trendCat ? t.categories[trendCat] || 0 : t.total));
    const max = Math.max(...vals, 1);
    const prevH = box._h || {};
    box._h = {};
    box.innerHTML = "";
    dash.trend.forEach((t, i) => {
      const target = (vals[i] / max * 82).toFixed(2) + "%";
      box._h[t.month] = target;
      const col = document.createElement("div");
      col.className = "vbar";
      col.tabIndex = 0;
      col.setAttribute("role", "img");
      col.setAttribute("aria-label", `${monthLabel(t.month)}: ${inr(vals[i])}`);
      col.innerHTML = `<span class="vbar-val">${inr(vals[i])}</span><span class="vbar-fill" data-h="${target}" style="height:${prevH[t.month] || "0%"};--i:${i}"></span>`;
      const prev = i ? vals[i - 1] : null;
      const change = prev ? ((vals[i] - prev) / prev * 100) : null;
      const top = Object.entries(t.categories).sort((a, b) => b[1] - a[1]).slice(0, 3);
      bindTip(col, `<b>${esc(monthLabel(t.month))} · ${esc(trendCat || "All spending")}</b>
        <div class="tip-row"><span>Spend</span><span>${inr(vals[i])}</span></div>` +
        (change == null ? "" : `<div class="tip-row"><span>vs previous month</span><span>${change >= 0 ? "+" : ""}${change.toFixed(1)}%</span></div>`) +
        (trendCat ? "" : top.map(([c, v]) => `<div class="tip-row"><span>${esc(c)}</span><span>${inr(v)}</span></div>`).join("")));
      box.appendChild(col);
    });
    let axis = box.nextElementSibling;
    if (!axis || !axis.classList.contains("vbar-axis")) {
      axis = document.createElement("div");
      axis.className = "vbar-axis";
      box.after(axis);
    }
    axis.innerHTML = dash.trend.map((t) => `<span>${esc(monthLabel(t.month))}</span>`).join("");
    growWhenVisible(box);
  }

  function renderBudgets() {
    const form = $("#budgetForm");
    if (form.contains(document.activeElement)) return;
    form.innerHTML = state.spend_categories.map((c) => `
      <label>${esc(c)}<input type="number" min="0" step="500" inputmode="numeric" name="${esc(c)}" placeholder="No limit"
        value="${state.budgets[c] ? Math.round(state.budgets[c]) : ""}"></label>`).join("") +
      `<button class="btn btn-dark" type="submit">Save budgets</button>`;
  }

  function renderParseReport() {
    const st = state.statement, r = st.report, cs = st.cat_stats || {};
    const mapping = Object.entries(r.column_mapping).map(([k, v]) => `${k} ← “${v}”`).join("  ·  ");
    $("#parseReport").innerHTML = `
      <dl class="kv">
        <div><dt>Rows read</dt><dd>${r.rows_read}</dd></div><div><dt>Valid</dt><dd>${r.rows_valid}</dd></div>
        <div><dt>Dropped</dt><dd>${r.rows_dropped}</dd></div><div><dt>Header row</dt><dd>${r.header_row}</dd></div>
      </dl>
      <p><b>Column mapping</b> (source: ${esc(r.mapping_source)}): ${esc(mapping)}</p>
      ${Object.keys(r.drop_reasons).length ? `<p><b>Dropped because:</b> ${esc(JSON.stringify(r.drop_reasons))}</p>` : ""}
      <p><b>Categorizer:</b> ${cs.rule || 0} rows by rule, ${cs.llm || 0} by the model, ${cs.fallback || 0} fallback,
        ${cs.user || 0} corrected by you (${cs.llm_calls || 0} model call${cs.llm_calls === 1 ? "" : "s"}).</p>
      ${cs.llm_error ? `<p class="warn-box">Model categorisation failed, so rules and fallbacks were used: ${esc(cs.llm_error)}</p>` : ""}
      ${(st.prompts || []).map((p) => `<p><b>Prompt: <code>${esc(p.name)}</code></b></p><pre>${esc("SYSTEM\n" + p.system + "\n\nUSER\n" + p.user)}</pre>`).join("")}`;
  }

  function renderTransactions() {
    const q = $("#txnSearch").value.trim().toLowerCase();
    const rows = q ? txns.filter((t) => `${t.date} ${t.merchant} ${t.category} ${t.type} ${t.source} ${t.description}`.toLowerCase().includes(q)) : txns;
    const opts = (cur) => state.categories.map((c) => `<option${c === cur ? " selected" : ""}>${esc(c)}</option>`).join("");
    $("#txnTable tbody").innerHTML = rows.map((t) => `
      <tr>
        <td>${esc(t.date)}</td><td>${esc(t.merchant)}</td>
        <td class="num ${t.type === "credit" ? "credit" : ""}">${t.type === "credit" ? "+" : ""}${inr(t.amount)}</td>
        <td>${esc(t.type)}</td>
        <td><select data-merchant="${esc(t.merchant)}" aria-label="Category for ${esc(t.merchant)}">${opts(t.category)}</select></td>
        <td><span class="pill ${esc(t.source)}">${esc(t.source === "llm" ? "model" : t.source)}</span></td>
        <td class="desc" title="${esc(t.description)}">${esc(t.description)}</td>
      </tr>`).join("");
    $("#txnCount").textContent = `${rows.length} of ${txns.length} transactions`;
  }

  // ------------------------------------------------------------------ markdown (escaped first, then a small safe subset)
  function inline(s) {
    return esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(>])\*([^*\s][^*]*?)\*(?=[\s).,;:!?<]|$)/g, "$1<em>$2</em>")
      .replace(/(^|[\s(>])_([^_\s][^_]*?)_(?=[\s).,;:!?<]|$)/g, "$1<em>$2</em>");
  }
  function md(src) {
    const lines = String(src || "").replace(/\r/g, "").split("\n");
    const out = [];
    const isRow = (l) => /^\s*\|.*\|\s*$/.test(l);
    const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    const UL = /^\s*[-*•]\s+(.*)$/, OL = /^\s*\d+[.)]\s+(.*)$/;
    let i = 0;
    while (i < lines.length) {
      const l = lines[i];
      if (!l.trim()) { i++; continue; }
      if (isRow(l) && i + 1 < lines.length && /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(lines[i + 1])) {
        const head = cells(l); i += 2;
        const body = [];
        while (i < lines.length && isRow(lines[i])) body.push(cells(lines[i++]));
        out.push(`<div class="tbl-scroll"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${
          body.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      const h = l.match(/^\s*(#{1,6})\s+(.*)$/);
      if (h) { out.push(`<h4>${inline(h[2])}</h4>`); i++; continue; }
      if (/^\s*>/.test(l)) {
        const buf = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
        out.push(`<blockquote>${inline(buf.join(" "))}</blockquote>`);
        continue;
      }
      if (UL.test(l) || OL.test(l)) {
        const ordered = OL.test(l);
        const items = [];
        while (i < lines.length && lines[i].trim()) {
          const cur = lines[i];
          const top = (ordered ? OL : UL).exec(cur);
          if (top && !(ordered && /^\s{2,}/.test(cur))) items.push({ text: top[1], sub: [] });
          else if (UL.test(cur) && items.length) items[items.length - 1].sub.push(UL.exec(cur)[1]);
          else if (/^\s{2,}\S/.test(cur) && items.length) items[items.length - 1].text += " " + cur.trim();
          else break;
          i++;
          while (i < lines.length && !lines[i].trim() && i + 1 < lines.length && (ordered ? OL : UL).test(lines[i + 1])) i++;
        }
        const tag = ordered ? "ol" : "ul";
        out.push(`<${tag}>${items.map((it) => `<li>${inline(it.text)}${it.sub.length ? `<ul>${it.sub.map((s) => `<li>${inline(s)}</li>`).join("")}</ul>` : ""}</li>`).join("")}</${tag}>`);
        continue;
      }
      const buf = [];
      while (i < lines.length && lines[i].trim() && !isRow(lines[i]) && !UL.test(lines[i]) && !OL.test(lines[i]) && !/^\s*(#{1,6}\s|>)/.test(lines[i])) {
        buf.push(inline(lines[i].trim()) + (/ {2,}$/.test(lines[i]) ? "<br>" : " "));
        i++;
      }
      out.push(`<p>${buf.join("").trim()}</p>`);
    }
    return out.join("");
  }

  // ------------------------------------------------------------------ chat
  function renderChips() {
    $("#chips").innerHTML = SUGGESTED.map(([k, q]) => `<button class="chip" type="button" data-q="${esc(q)}"><span>${k}</span>${esc(q)}</button>`).join("");
  }

  const pretty = (s) => {
    try { s = JSON.stringify(JSON.parse(s), null, 2); } catch { /* keep as is */ }
    return s.length > 3000 ? s.slice(0, 3000) + "\n… (truncated)" : s;
  };

  function traceHTML(tr) {
    const used = tr.retrieval?.length > 0;
    const ref = tr.reflection || {};
    const thoughts = (tr.steps || []).filter((s) => s.thought);
    const overview = `
      <dl class="trace-kv">
        <dt>Intent</dt><dd><code>${esc(tr.intent)}</code> via ${esc(tr.router_source || "—")}</dd>
        <dt>Reason</dt><dd>${esc(tr.reason || "—")}</dd>
        <dt>Handler</dt><dd>${esc(tr.handler || "—")}</dd>
        <dt>Retrieval used</dt><dd><b>${used ? "yes" : "no"}</b></dd>
        <dt>Original question</dt><dd>${esc(tr.question)}</dd>
        <dt>Standalone question</dt><dd>${esc(tr.rewritten || tr.question)}</dd>
        ${(tr.guardrails || []).length ? `<dt>Guardrails</dt><dd>${tr.guardrails.map(esc).join("<br>")}</dd>` : ""}
      </dl>
      ${thoughts.map((s) => `<p class="muted"><i>Step ${s.step} thought:</i> ${esc(s.thought)}</p>`).join("")}
      ${(tr.errors || []).length ? `<p class="warn-box">${tr.errors.map(esc).join("<br>")}</p>` : ""}`;
    const prompts = (tr.prompts || []).map((p) => `<h5>${esc(p.name)} · system prompt</h5><pre>${esc(p.system)}</pre><h5>${esc(p.name)} · user prompt</h5><pre>${esc(p.user)}</pre>`).join("") || "<p>No prompts recorded.</p>";
    const tools = (tr.tool_calls || []).map((c) => `<h5>Step ${c.step} · ${esc(c.tool)}</h5><p><code>${esc(c.tool)}(${esc(JSON.stringify(c.args))})</code></p><pre>${esc(pretty(c.result))}</pre>`).join("") || "<p>No tools called.</p>";
    const retrieval = (tr.retrieval || []).map((r) => {
      const guide = r.collection === "budget_guidance";
      const head = guide ? ["Similarity", "Source", "Text"] : ["Similarity", "Date", "Merchant", "Amount", "Category"];
      const rows = (r.chunks || []).map((c) => guide
        ? [c.similarity, c.source, (c.text || "").slice(0, 220) + ((c.text || "").length > 220 ? "…" : "")]
        : [c.similarity, c.date, c.merchant, inr(c.amount), c.category]);
      return `<h5>Collection ${esc(r.collection)}</h5><p>Query: “${esc(r.query)}”</p>
        <table class="mini-table"><thead><tr>${head.map((x) => `<th>${x}</th>`).join("")}</tr></thead>
        <tbody>${rows.map((row) => `<tr>${row.map((x) => `<td>${esc(x)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    }).join("") || "<p>Retrieval used: <b>no</b>. The agent did not need semantic search for this question.</p>";
    const reflection = `
      <dl class="trace-kv">
        <dt>Verdict</dt><dd><b>${esc(ref.verdict || "—")}</b></dd>
        <dt>Numbers not found in tool outputs</dt><dd>${esc((ref.code_check_flags || []).join(", ") || "none")}</dd>
        <dt>Model critique used</dt><dd>${ref.llm_used ? "yes" : "no"}</dd>
        ${ref.final_flags ? `<dt>Still unverified after revision</dt><dd>${esc(ref.final_flags.join(", ") || "none")}</dd>` : ""}
        ${ref.removed_citations?.length ? `<dt>Citations removed</dt><dd>${esc(ref.removed_citations.join(", "))}</dd>` : ""}
        <dt>Notes</dt><dd>${esc(ref.notes || "—")}</dd>
      </dl>`;
    const panes = { Overview: overview, Prompts: prompts, "Tool calls": tools, Retrieval: retrieval, Reflection: reflection };
    const names = Object.keys(panes);
    return `<details class="trace"><summary>Agent trace</summary>
      <div class="trace-tabs" role="tablist">${names.map((n, i) => `<button type="button" role="tab" aria-selected="${i === 0}" data-pane="${i}">${n}</button>`).join("")}</div>
      ${names.map((n, i) => `<div class="trace-pane" data-pane="${i}"${i ? " hidden" : ""}>${panes[n]}</div>`).join("")}
    </details>`;
  }

  function botHTML(answer, tr) {
    let meta = "", matched = "";
    if (tr) {
      const used = tr.retrieval?.length > 0;
      const n = (tr.tool_calls || []).length;
      meta = `<div class="msg-meta">
        <span class="tag">${esc((tr.intent || "").replace(/_/g, " "))}</span>
        <span class="tag ${used ? "yes" : ""}">Retrieval: ${used ? "yes" : "no"}</span>
        <span class="tag">${n} tool call${n === 1 ? "" : "s"}</span>
        ${tr.reflection?.verdict ? `<span class="tag">Check: ${esc(tr.reflection.verdict.split(" (")[0])}</span>` : ""}
      </div>`;
      if (tr.matched_rows?.length) {
        matched = `<p class="muted small" style="margin-top:14px">Transactions the agent retrieved (the answer names the relevant one)</p>
          <table class="mini-table"><thead><tr><th>Date</th><th>Merchant</th><th>Amount</th><th>Category</th></tr></thead><tbody>${
          tr.matched_rows.map((r) => `<tr><td>${esc(r.date)}</td><td>${esc(r.merchant)}</td><td>${inr(r.amount)}</td><td>${esc(r.category)}</td></tr>`).join("")}</tbody></table>`;
      }
    }
    return `${meta}<div class="md">${md(answer)}</div>${matched}${tr ? traceHTML(tr) : ""}`;
  }

  function addMsg(role, html) {
    const d = document.createElement("div");
    d.className = role === "user" ? "msg-user" : "msg-bot";
    d.innerHTML = html;
    $("#thread").appendChild(d);
    return d;
  }

  function restoreThread() {
    $("#thread").innerHTML = "";
    (state.messages || []).forEach((m) => addMsg(m.role, m.role === "user" ? esc(m.content) : botHTML(m.content, m.trace)));
  }

  async function ask(q) {
    q = (q || "").trim();
    if (!q || asking) return;
    if (!state.statement) { toast("Load a statement first."); go("home"); return; }
    if (!state.llm.ok) { toast("Chat needs a model. Add your API key and model to .env."); return; }
    asking = true;
    $("#btnSend").disabled = true;
    $("#question").value = "";
    addMsg("user", esc(q));
    const stages = ["Rewriting the question", "Routing by intent", "Agent choosing tools", "Running pandas tools", "Checking every number"];
    let si = 0;
    const wait = addMsg("bot", `<div class="thinking"><span class="busy-bars"><i></i><i></i><i></i><i></i></span><span class="stage">${stages[0]}…</span></div>`);
    const timer = setInterval(() => {
      if (si >= stages.length - 1) return;
      swapText($(".stage", wait), stages[++si] + "…");
    }, 1600);
    wait.scrollIntoView({ block: "end", behavior: "smooth" });
    try {
      const { answer, trace } = await api("/api/chat", { json: { question: q } });
      wait.classList.add("fresh");          // the answer's parts rise in, one after another
      wait.innerHTML = botHTML(answer, trace);
    } catch (e) {
      wait.classList.add("fresh");
      wait.innerHTML = `<p class="warn-box">${esc(e.message)}</p>`;
    } finally {
      clearInterval(timer);
      asking = false;
      $("#btnSend").disabled = false;
      wait.scrollIntoView({ block: "nearest", behavior: "smooth" });
      $("#question").focus({ preventScroll: true });
    }
  }

  // ------------------------------------------------------------------ events
  function wire() {
    addEventListener("hashchange", () => go(location.hash.slice(1)));
    addEventListener("scroll", onScroll, { passive: true });
    $$("[data-nav]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); go(a.dataset.nav); }));

    $("#navSample").addEventListener("click", () => {
      const target = ctaTarget()[1];
      if (target === "sample") loadStatement("sample"); else go(target);
    });
    $("#btnSample").addEventListener("click", () => loadStatement("sample"));
    $$('[data-action="sample"]').forEach((b) => b.addEventListener("click", () => loadStatement("sample")));
    $("#btnUpload").addEventListener("click", () => pendingFile && loadStatement("upload"));

    const drop = $("#drop");
    $("#file").addEventListener("change", (e) => setFile(e.target.files[0]));
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (e) => {
      const f = e.dataTransfer.files[0];
      if (f && /\.(csv|pdf)$/i.test(f.name)) setFile(f); else toast("Drop a CSV or a text-based PDF statement.");
    });

    $("#modelForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        state = await api("/api/llm", { json: { provider: $("#provider").value, model: $("#model").value } });
        renderState();
        toast(state.llm.ok ? `Model set to ${state.llm.provider} · ${state.llm.model}` : `Model not available: ${state.llm.error}`);
      } catch (err) { toast(err.message); }
    });
    $("#provider").addEventListener("change", () => ($("#model").value = ""));

    $("#periodSeg").addEventListener("click", async (e) => {
      const b = e.target.closest("button[data-period]");
      if (!b || b.dataset.period === period) return;
      period = b.dataset.period;
      // move the selection immediately; numbers and bars follow when the data arrives
      $$("button[data-period]", e.currentTarget).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      glide(e.currentTarget, b);
      try { await loadDashboard(); } catch (err) { toast(err.message); }
    });
    $("#trendCat").addEventListener("change", (e) => { trendCat = e.target.value; renderTrend(); });
    $("#txnSearch").addEventListener("input", renderTransactions);

    $("#txnTable").addEventListener("change", async (e) => {
      const sel = e.target.closest("select[data-merchant]");
      if (!sel) return;
      try {
        const r = await api("/api/category", { json: { merchant: sel.dataset.merchant, category: sel.value } });
        state.statement.cat_stats = r.cat_stats;
        renderParseReport();
        await Promise.all([loadDashboard(), loadTransactions()]);
        toast(`${sel.dataset.merchant} → ${sel.value} (${r.updated} row${r.updated === 1 ? "" : "s"} updated)`);
      } catch (err) { toast(err.message); loadTransactions(); }
    });

    $("#budgetForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const budgets = {};
      $$("input", e.target).forEach((i) => { if (Number(i.value) > 0) budgets[i.name] = Number(i.value); });
      try {
        state.budgets = (await api("/api/budgets", { json: { budgets } })).budgets;
        document.activeElement.blur();
        await loadDashboard();
        toast("Budgets saved. Alerts updated.");
      } catch (err) { toast(err.message); }
    });

    $("#btnReset").addEventListener("click", async () => {
      if (!confirm("Clear this statement, the chat and its ChromaDB collection?")) return;
      try {
        state = await api("/api/reset", { method: "POST" });
        dash = null; txns = []; period = ""; trendCat = "";
        $("#thread").innerHTML = "";
        setFile(null);
        $("#file").value = "";
        renderState();
        go("home");
      } catch (err) { toast(err.message); }
    });

    $("#chips").addEventListener("click", (e) => { const c = e.target.closest(".chip"); if (c) ask(c.dataset.q); });
    $("#composer").addEventListener("submit", (e) => { e.preventDefault(); ask($("#question").value); });
    $("#thread").addEventListener("click", (e) => {
      const b = e.target.closest(".trace-tabs button");
      if (!b) return;
      const trace = b.closest(".trace");
      $$(".trace-tabs button", trace).forEach((x) => x.setAttribute("aria-selected", x === b));
      $$(".trace-pane", trace).forEach((p) => (p.hidden = p.dataset.pane !== b.dataset.pane));
      glide(b.parentElement, b);
    });
    // place a trace's glider when it is first opened (it has no size while closed)
    $("#thread").addEventListener("toggle", (e) => {
      const d = e.target;
      if (!d.matches?.("details.trace") || !d.open) return;
      const tabs = $(".trace-tabs", d);
      glide(tabs, $("button[aria-selected='true']", tabs), { animate: false });
    }, true);
    let gt;
    addEventListener("resize", () => { clearTimeout(gt); gt = setTimeout(reglideAll, 120); });
    document.fonts?.ready.then(reglideAll);
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    wire();
    // static sections fade up as they scroll into view
    reveal($$(".band .two-col > *"));
    reveal($$(".steps .step"));
    reveal($$(".model-block"));
    reveal($$("#view-dashboard .grid-2 > .panel, #view-dashboard .block-head, #view-dashboard .fold"), { stagger: false });
    reveal($$("#view-about .prose, #view-about .block-head, #view-about .table-wrap"), { stagger: false });
    go(location.hash.slice(1) || "home");
    try {
      state = await api("/api/state");
      renderState();
      restoreThread();
      if (state.statement) await Promise.all([loadDashboard(), loadTransactions()]);
    } catch (e) {
      $("#llmLine").textContent = e.message;
      $("#llmLine").classList.add("bad");
    }
  }
  boot();
})();
