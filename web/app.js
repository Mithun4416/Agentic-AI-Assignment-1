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
  let toastTimer;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), 6500);
  }

  let busyTimer;
  function busy(title, steps = []) {
    $("#busyTitle").textContent = title;
    let i = 0;
    $("#busyStep").textContent = steps[0] || "";
    clearInterval(busyTimer);
    if (steps.length > 1) busyTimer = setInterval(() => ($("#busyStep").textContent = steps[++i % steps.length]), 1700);
    $("#busy").hidden = false;
  }
  function idle() {
    clearInterval(busyTimer);
    $("#busy").hidden = true;
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
    if (location.hash.slice(1) !== view) history.replaceState(null, "", "#" + view);
    window.scrollTo(0, 0);
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
  const flow = (() => {
    const c = $("#flow");
    const ctx = c.getContext("2d");
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const colors = ["#DDB892", "#B08968", "#E6CCB2", "#9C6644", "#DDB892", "#B08968"];
    const STEP = 9;
    let w = 0, h = 0, strands = [], raf = 0, t = 0;

    // Each strand is a streamline traced through a slowly drifting flow field, redrawn every frame
    // (no fade trick, so no residue). Additive blending makes converging strands glow like a river.
    const angle = (x, y) => {
      const k = 0.0015;
      return Math.PI * 0.4 + 1.05 * (Math.sin(x * k * 1.3 + t) + Math.cos(y * k * 1.9 - t * 0.7) + Math.sin((x + y) * k * 0.7 + t * 0.4));
    };
    function draw() {
      ctx.globalCompositeOperation = "source-over";
      ctx.globalAlpha = 1;
      ctx.fillStyle = "#22150D";
      ctx.fillRect(0, 0, w, h);
      ctx.globalCompositeOperation = "lighter";
      ctx.lineCap = "round";
      for (const s of strands) {
        let x = s.x, y = s.y;
        ctx.beginPath();
        ctx.moveTo(x, y);
        for (let i = 0; i < s.len; i++) {
          const a = angle(x, y);
          x += Math.cos(a) * STEP; y += Math.sin(a) * STEP;
          ctx.lineTo(x, y);
          if (x < -40 || x > w + 40 || y > h + 40) break;
        }
        ctx.globalAlpha = s.a;
        ctx.lineWidth = s.lw;
        ctx.strokeStyle = s.c;
        ctx.stroke();
      }
    }
    function loop() {
      if (document.body.dataset.view !== "home" || document.hidden) { raf = 0; return; }
      t += 0.0016;
      draw();
      raf = requestAnimationFrame(loop);
    }
    function resize() {
      const dpr = Math.min(devicePixelRatio || 1, 2);
      w = c.clientWidth; h = c.clientHeight;
      if (!w || !h) return;
      c.width = w * dpr; c.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const n = Math.round(Math.max(110, Math.min(380, (w * h) / 3400)));
      strands = Array.from({ length: n }, (_, i) => {
        const bright = i % 9 === 0;   // a few strong strands, many fine ones
        return {
          x: Math.random() * w * 1.2 - w * 0.1, y: Math.random() * h * 1.1 - h * 0.25,
          len: 50 + ((Math.random() * 150) | 0), c: colors[i % colors.length],
          a: bright ? 0.5 + Math.random() * 0.3 : 0.07 + Math.random() * 0.2,
          lw: bright ? 1.4 + Math.random() * 0.8 : 0.6 + Math.random() * 0.5,
        };
      });
      draw();
    }
    function start() {
      if (!w) resize();
      if (!reduce && !raf) raf = requestAnimationFrame(loop);
    }
    let rt;
    addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(resize, 150); });
    document.addEventListener("visibilitychange", start);
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

    $("#periodSeg").innerHTML = [["", "All months"], ...months.map((x) => [x, monthLabel(x)])]
      .map(([v, l]) => `<button type="button" data-period="${esc(v)}" aria-pressed="${v === period}"><i class="dot"></i>${esc(l)}</button>`).join("");

    const rate = m.savings_rate_pct;
    $("#tiles").innerHTML = [
      ["Income", inr(m.income), m.other_credits ? `plus ${inr(m.other_credits)} other credits` : "salary and interest", ""],
      ["Spend", inr(m.expense), m.invested ? `${inr(m.invested)} invested separately` : "excluding investments", ""],
      ["Left over", inr(m.net_after_expense), "income minus spend", ""],
      ["Savings rate", rate == null ? "—" : rate.toFixed(1) + "%", rate == null ? "no income in this period" : rate >= 20 ? "meets the 20% guideline" : "below the 20% guideline", "dark"],
    ].map(([label, value, sub, cls]) =>
      `<div class="tile ${cls}"><div class="tile-label">${label}</div><div class="tile-value" title="${esc(value)}">${esc(value)}</div><div class="tile-sub">${esc(sub)}</div></div>`).join("");

    // categories: sorted horizontal bars, one hue (magnitude, not identity)
    const cats = breakdown.categories || [];
    const box = $("#catBars");
    box.innerHTML = "";
    $("#catNote").textContent = cats.length ? `${inr(breakdown.total_spend)} total` : "";
    if (!cats.length) box.innerHTML = `<p class="chart-empty">No spending in this period.</p>`;
    const max = Math.max(...cats.map((c) => c.amount), 1);
    cats.forEach((c) => {
      const row = document.createElement("div");
      row.className = "hbar";
      row.tabIndex = 0;
      row.setAttribute("role", "img");
      row.setAttribute("aria-label", `${c.category}: ${inr(c.amount)}, ${c.share_pct}% of spend, ${c.count} transactions`);
      row.innerHTML = `<span class="hbar-name">${esc(c.category)}</span>
        <span class="hbar-track"><span class="hbar-fill" style="width:${(c.amount / max * 100).toFixed(2)}%;display:block"></span></span>
        <span class="hbar-val">${inr(c.amount)}<span>${c.share_pct.toFixed(1)}%</span></span>`;
      bindTip(row, `<b>${esc(c.category)}</b><div class="tip-row"><span>Amount</span><span>${inr(c.amount)}</span></div>
        <div class="tip-row"><span>Share of spend</span><span>${c.share_pct.toFixed(1)}%</span></div>
        <div class="tip-row"><span>Transactions</span><span>${c.count}</span></div>`);
      box.appendChild(row);
    });

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

    renderBudgets();
  }

  function renderTrend() {
    const box = $("#trendBars");
    const vals = dash.trend.map((t) => (trendCat ? t.categories[trendCat] || 0 : t.total));
    const max = Math.max(...vals, 1);
    box.innerHTML = "";
    dash.trend.forEach((t, i) => {
      const col = document.createElement("div");
      col.className = "vbar";
      col.tabIndex = 0;
      col.setAttribute("role", "img");
      col.setAttribute("aria-label", `${monthLabel(t.month)}: ${inr(vals[i])}`);
      col.innerHTML = `<span class="vbar-val">${inr(vals[i])}</span><span class="vbar-fill" style="height:${(vals[i] / max * 82).toFixed(2)}%"></span>`;
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
    const timer = setInterval(() => { si = Math.min(si + 1, stages.length - 1); $(".stage", wait).textContent = stages[si] + "…"; }, 1500);
    wait.scrollIntoView({ block: "end", behavior: "smooth" });
    try {
      const { answer, trace } = await api("/api/chat", { json: { question: q } });
      wait.innerHTML = botHTML(answer, trace);
    } catch (e) {
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
      if (!b) return;
      period = b.dataset.period;
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
    });
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    wire();
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
