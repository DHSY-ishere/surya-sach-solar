const API = (window.SURYA_API || "").replace(/\/$/, "");
const WA = window.SURYA_WA || "";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const f1 = (n) => Number(n).toFixed(1);
const inr = (n) => Number(n).toLocaleString("en-IN");
const qs = new URLSearchParams(location.search);
let systems = [], current = 0, live = false;

async function load() {
  try {   // owner's private link from WhatsApp: ?t=<token>
    if (API && qs.get("t")) {
      const r = await fetch(`${API}/api/me?t=${encodeURIComponent(qs.get("t"))}`);
      if (r.ok) { systems = [await r.json()]; live = true; return; }
    }
    if (API) {
      const r = await fetch(`${API}/api/systems`);
      if (r.ok) { systems = (await r.json()).systems; live = true; return; }
    }
  } catch (e) {}
  systems = (await (await fetch("sample.json")).json()).systems;
}

function picker() {
  $("picker").innerHTML = systems.length < 2 ? "" : systems.map((s, i) =>
    `<button role="tab" aria-pressed="${i === current}" data-i="${i}"><span class="dot ${s.diagnosis && s.diagnosis.status === "short" ? "short" : ""}"></span>${esc(s.name)}, ${esc((s.place || "").split(",")[0])}</button>`).join("");
  $("picker").querySelectorAll("button").forEach((b) => b.onclick = () => { current = +b.dataset.i; picker(); render(); });
}

function render() {
  const s = systems[current], rows = s.readings, dg = s.diagnosis;
  if (!rows.length || !dg) { $("view").innerHTML = '<p class="headline">No readings yet. Send your inverter photo on WhatsApp.</p>'; return; }
  const last = rows[rows.length - 1];
  const avgA = rows.reduce((a, r) => a + r.actual, 0) / rows.length;
  const avgE = rows.reduce((a, r) => a + (r.band_low + r.band_high) / 2, 0) / rows.length;
  const short = (1 - avgA / avgE) * 100;
  const below = rows.filter((r) => r.actual < r.band_low).length;
  const top = dg.findings[0];
  const head = dg.status === "healthy"
    ? ["This roof is healthy.", "ok", `Last ${rows.length} days average ${f1(avgA)} units against ${f1(avgE)} expected. Nothing to fix.`]
    : [`This roof is ${Math.round(Math.abs(short))}% short.`, "bad",
       top ? top.advice : `Averaging ${f1(avgA)} units a day against ${f1(avgE)} expected.`];
  const promised = s.promised_kwh, pc = s.promise_check;

  $("view").innerHTML = `
  <div class="headline"><h1 class="${head[1]}">${head[0]}</h1><p>${esc(head[2])}</p>
    ${s.simulated ? '<p class="banner">Demonstration system: real weather history for this city, simulated readings with an injected fault.</p>' : ""}</div>

  <div class="ledger">
    <div><b>${f1(last.actual)}</b><span>units on ${esc(last.date)}</span></div>
    <div><b>${f1(last.band_low)}–${f1(last.band_high)}</b><span>healthy range that day</span></div>
    <div><b>${f1(avgA)}</b><span>average units a day, against ${f1(avgE)} expected</span></div>
    <div><b>${below} of ${rows.length}</b><span>days below the healthy range</span></div>
    <div><b>Rs ${inr(dg.lost_inr_month)}</b><span>lost each month at Rs ${esc(s.tariff)}/unit</span></div>
  </div>

  <div class="panel"><h2>What it made, against what it should have</h2>
    <p class="sub">${esc(s.kwp)} kW at ${esc(s.place || "your location")}, tilt ${esc(s.tilt)}°. The pale band is the physics: the sunlight that fell on this roof that day, a performance ratio of 0.72 to 0.80, derated for heat. Blue ticks mark rain.</p>
    <div class="chartbox" id="chartbox">${chart(rows)}<div class="tip" id="tip"></div></div>
    <div class="legend"><span><i></i>actual units</span><span><i class="band"></i>healthy range</span><span><i class="rain"></i>rain of 5 mm or more</span></div>
  </div>

  <div class="panel"><h2>${dg.findings.length ? "Why" : "Diagnosis"}</h2>
    <div class="cards">${dg.findings.length ? dg.findings.map((f) => `<div class="card"><h3>${esc(f.cause.replace(/_/g, " "))}</h3>
      <div class="conf" title="confidence"><i style="width:${Math.round(f.confidence * 100)}%"></i></div>
      <p>${esc(f.advice)}</p><p>Confidence ${Math.round(f.confidence * 100)}%</p></div>`).join("")
      : '<div class="card"><h3>No fault found</h3><p>Output sits inside the healthy range on sunny days. Keep sending a reading each evening and this will tell you the day that changes.</p></div>'}</div>
  </div>

  <div class="panel two">
    <div><h2>What was promised</h2>${promised ? promiseBlock(s, promised, pc) : '<p class="sub">No promise on file. On WhatsApp, type “promise 2 13” to check any claim.</p>'}</div>
    <div><h2>Take it to the vendor</h2>
      <p class="sub">${esc(s.vendor ? s.vendor + " installed this system. " : "")}Send <b>report</b> on WhatsApp for a 30-day PDF with a hash of every photo and a QR code. Anyone who scans it sees whether it was altered.</p>
      <p class="sub">${rows.filter((r) => r.photo_sha256).length} of ${rows.length} readings here are backed by a stored photo. ${rows.filter((r) => r.ghi_nasa).length} have been confirmed against NASA POWER.</p></div>
  </div>


  ${WA ? `<div class="panel cta"><div><h2>Get this for your own roof</h2>
    <p class="sub">Message Surya Sach on WhatsApp and send each evening's reading. Type <b>dashboard</b> and it replies with a private link to a page like this one, built from your readings. Type <b>report</b> for the evidence PDF.</p></div>
    <div><a class="btn solid" href="${window.waLink("hi")}" target="_blank" rel="noopener">Open WhatsApp</a>
    <p class="note">+${esc(WA)}. During the hackathon this is a WhatsApp test number: it replies only to registered testers, so the demo video shows it live.</p></div></div>` : ""}

  <div class="panel"><h2>Every reading</h2>
    <div class="scroll"><table class="rd"><thead><tr><th>Date</th><th>Made</th><th>Healthy range</th><th>Of expected</th><th>Rain mm</th><th>Sun kWh/m²</th></tr></thead><tbody>
    ${rows.slice().reverse().map((r) => `<tr><td>${esc(r.date)}</td><td class="${r.actual < r.band_low ? "low" : ""}">${f1(r.actual)}</td><td>${f1(r.band_low)}–${f1(r.band_high)}</td><td>${Math.round(r.ratio * 100)}%</td><td>${r.rain_mm ?? "–"}</td><td>${r.ghi ? Number(r.ghi).toFixed(2) : "–"}</td></tr>`).join("")}
    </tbody></table></div>${live ? "" : '<p class="note">Showing the built-in demonstration data. Deploy the API and this page switches to live systems automatically.</p>'}</div>`;
  wireTip(rows);
}

function promiseBlock(s, promised, pc) {
  if (!pc) return `<p class="sub">${esc(promised)} units/day promised from ${esc(s.kwp)} kW.</p>`;
  const max = Math.max(promised, pc.peak_month_ceiling_kwh_day) * 1.1, p = (x) => (x / max) * 100;
  const verdict = { impossible: "physically impossible", peak_only: "possible only in peak summer", possible: "believable" }[pc.verdict];
  return `<p class="sub">${esc(promised)} units a day from ${esc(s.kwp)} kW is <b>${verdict}</b> here. The ceiling is ${f1(pc.ceiling_kwh_day)} on an average day and ${f1(pc.peak_month_ceiling_kwh_day)} in the best month.</p>
    <div class="claimbar"><i style="left:0;width:${p(pc.typical_kwh_day)}%;background:#fff;opacity:.45"></i><i style="left:${p(pc.typical_kwh_day)}%;width:${p(pc.peak_month_ceiling_kwh_day - pc.typical_kwh_day)}%;background:var(--sun);opacity:.35"></i><b style="left:${p(promised)}%" title="promised"></b></div>
    <p class="note">Red marker: promised ${f1(promised)}. White: fair expectation up to ${f1(pc.typical_kwh_day)}. Yellow: the stretch up to the best-month ceiling.</p>`;
}

const W = 880, H = 300, L = 38, B = 30, T = 14, R = 10;
let geo = null;
function chart(rows) {
  const top = Math.ceil(Math.max(...rows.map((r) => Math.max(r.actual, r.band_high)))) + 1;
  const x = (i) => L + (i / Math.max(rows.length - 1, 1)) * (W - L - R);
  const y = (v) => H - B - (v / top) * (H - B - T);
  geo = { x, y, n: rows.length };
  const hi = rows.map((r, i) => `${x(i)},${y(r.band_high)}`), lo = rows.map((r, i) => `${x(i)},${y(r.band_low)}`).reverse();
  let grid = "";
  for (let v = 0; v <= top; v += 2) grid += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--rule)"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--ink-2)">${v}</text>`;
  const rain = rows.map((r, i) => (r.rain_mm >= 5 ? `<line x1="${x(i)}" x2="${x(i)}" y1="${T}" y2="${H - B}" stroke="var(--rain)" stroke-width="3" opacity=".55"/>` : "")).join("");
  const line = rows.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(r.actual).toFixed(1)}`).join("");
  const dots = rows.map((r, i) => `<circle cx="${x(i)}" cy="${y(r.actual)}" r="${r.actual < r.band_low ? 3.6 : 2.4}" fill="${r.actual < r.band_low ? "var(--bad)" : "var(--sun)"}"/>`).join("");
  const step = Math.max(1, Math.floor(rows.length / 6));
  const ticks = rows.map((r, i) => (i % step === 0 ? `<text x="${x(i)}" y="${H - 8}" text-anchor="middle" font-size="11" fill="var(--ink-2)">${esc(r.date.slice(5))}</text>` : "")).join("");
  return `<svg id="svg" viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" role="img" aria-label="Daily units against healthy range">${grid}${rain}
    <polygon points="${hi.concat(lo).join(" ")}" fill="#ffffff" opacity=".13"/><path d="${line}" fill="none" stroke="var(--sun)" stroke-width="2.5" stroke-linejoin="round"/>${dots}${ticks}
    <line id="cross" y1="${T}" y2="${H - B}" stroke="var(--ink)" stroke-width="1" style="display:none"/></svg>`;
}

function wireTip(rows) {
  const svg = $("svg"), tip = $("tip"), cross = $("cross"), box = $("chartbox");
  svg.addEventListener("mousemove", (e) => {
    const rect = svg.getBoundingClientRect(), vx = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.max(0, Math.min(rows.length - 1, Math.round(((vx - L) / (W - L - R)) * (rows.length - 1))));
    const r = rows[i];
    cross.setAttribute("x1", geo.x(i)); cross.setAttribute("x2", geo.x(i)); cross.style.display = "";
    tip.style.display = "block"; tip.style.left = `${(geo.x(i) / W) * rect.width}px`; tip.style.top = `${(geo.y(r.actual) / H) * rect.height}px`;
    tip.textContent = `${r.date}: ${f1(r.actual)} units (healthy ${f1(r.band_low)}–${f1(r.band_high)})${r.rain_mm >= 5 ? ` · rain ${r.rain_mm} mm` : ""}`;
  });
  svg.addEventListener("mouseleave", () => { tip.style.display = "none"; cross.style.display = "none"; });
}

load().then(() => { picker(); render(); }).catch(() => { $("view").innerHTML = '<p class="fatal">Could not load data.</p>'; });
