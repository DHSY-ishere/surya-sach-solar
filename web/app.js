const API = (window.SURYA_API || "").replace(/\/$/, "");
const $ = (id) => document.getElementById(id);
const fmt = (n, d = 1) => Number(n).toFixed(d);

// Same physics as app/engine/model.py, so the page still works if the API is unreachable.
function promiseCheckLocal(kwp, promised, annual, best) {
  const PR = 0.85, TILT = 1.08;
  const avg = kwp * annual * TILT * PR, peak = kwp * best * PR, typical = kwp * annual * 0.75;
  const verdict = promised > peak ? "impossible" : promised > avg ? "peak_only" : "possible";
  return { verdict, ceiling_kwh_day: avg, peak_month_ceiling_kwh_day: peak, typical_kwh_day: typical,
           overpromise_pct: promised > avg ? (promised / avg - 1) * 100 : 0 };
}

async function geocode(name) {
  const r = await fetch(`https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(name)}&count=1&country_code=IN`);
  const j = await r.json();
  if (!j.results || !j.results.length) throw new Error("place");
  return j.results[0];
}

async function check() {
  const kwp = parseFloat($("kwp").value), promised = parseFloat($("units").value);
  const v = $("verdict");
  if (!(kwp > 0) || !(promised > 0)) { v.innerHTML = "<p>Enter a size in kW and the units per day you were promised.</p>"; return; }
  v.innerHTML = "<p>Looking up the sunlight at your roof…</p>";
  let place = $("city").value || "Jaipur", lat = 26.91, lon = 75.79, res = null, source = "";
  try { const g = await geocode(place); lat = g.latitude; lon = g.longitude; place = g.name; } catch (e) {}
  try {
    if (!API) throw new Error("no api");
    const r = await fetch(`${API}/api/promise?kwp=${kwp}&promised=${promised}&lat=${lat}&lon=${lon}`);
    if (!r.ok) throw new Error("api");
    res = await r.json(); source = res.climate && res.climate.source;
  } catch (e) {
    try {
      const url = `https://power.larc.nasa.gov/api/temporal/climatology/point?parameters=ALLSKY_SFC_SW_DWN&community=RE&longitude=${lon}&latitude=${lat}&format=JSON`;
      const m = (await (await fetch(url)).json()).properties.parameter.ALLSKY_SFC_SW_DWN;
      const months = Object.entries(m).filter(([k, x]) => k !== "ANN" && x > 0).map(([, x]) => x);
      res = promiseCheckLocal(kwp, promised, m.ANN, Math.max(...months)); source = "NASA POWER climatology";
    } catch (e2) { res = promiseCheckLocal(kwp, promised, 5.4, 6.8); source = "India-wide averages"; }
  }
  render(kwp, promised, place, res, source);
}

function render(kwp, promised, place, r, source) {
  const head = { impossible: ["That promise is physically impossible.", "bad"],
                 peak_only: ["Only the sunniest month could do that.", "mid"],
                 possible: ["That's believable.", "ok"] }[r.verdict];
  const body = {
    impossible: `At ${place}, ${kwp} kW tops out at about <b>${fmt(r.ceiling_kwh_day)}</b> units on an average day and <b>${fmt(r.peak_month_ceiling_kwh_day)}</b> in the best month of the year, even with a perfect system. You were promised ${fmt(promised)}, which is ${Math.round(r.overpromise_pct)}% above the average ceiling. It was mis-sold before a single panel went up.`,
    peak_only: `The average-day ceiling is ${fmt(r.ceiling_kwh_day)} units; ${fmt(promised)} is only reachable in peak summer. A fair expectation is <b>${fmt(r.typical_kwh_day)}</b> units a day.`,
    possible: `The ceiling at ${place} is ${fmt(r.ceiling_kwh_day)} units, so ${fmt(promised)} is within reach. A fair expectation is <b>${fmt(r.typical_kwh_day)}</b> units a day.`,
  }[r.verdict];
  const max = Math.max(promised, r.peak_month_ceiling_kwh_day) * 1.15;
  const pos = (x) => `${(x / max) * 100}%`;
  const v = $("verdict");
  v.innerHTML = `<h2 class="${head[1]}">${head[0]}</h2><p>${body}</p>
    <div class="ruler" role="img" aria-label="Ruler of units per day">
      <div class="seg" style="left:0;width:${pos(r.typical_kwh_day)};background:#fff;opacity:.5"></div>
      <div class="seg" style="left:${pos(r.typical_kwh_day)};width:${(r.ceiling_kwh_day - r.typical_kwh_day) / max * 100}%;background:var(--ink-2);opacity:.45"></div>
      <div class="seg" style="left:${pos(r.ceiling_kwh_day)};width:${(r.peak_month_ceiling_kwh_day - r.ceiling_kwh_day) / max * 100}%;background:var(--sun);opacity:.45"></div>
      <div class="tick" style="left:${pos(r.typical_kwh_day)}"><span>fair<br>${fmt(r.typical_kwh_day)}</span></div>
      <div class="tick" style="left:${pos(r.ceiling_kwh_day)}"><span>average ceiling<br>${fmt(r.ceiling_kwh_day)}</span></div>
      <div class="tick" style="left:${pos(r.peak_month_ceiling_kwh_day)}"><span>best-month ceiling<br>${fmt(r.peak_month_ceiling_kwh_day)}</span></div>
      <div class="claim" style="left:${pos(promised)}"><span>promised ${fmt(promised)}</span></div>
    </div>
    <p class="note">Sunlight data: ${source || "NASA POWER"}. Ceilings assume a flawless system (performance ratio 0.85). Real roofs lose a little more.</p>`;
}

// ---- vendor scorecard
const SAMPLE = { vendors: [
  { vendor: "BrightGrid Energy", systems: 4, days: 120, score: 0.97, pct_underperforming: 0, preliminary: false, simulated: true },
  { vendor: "GreenVolt Solutions", systems: 3, days: 88, score: 0.86, pct_underperforming: 33, preliminary: false, simulated: true },
  { vendor: "SunnyRoofs Pvt Ltd", systems: 3, days: 90, score: 0.64, pct_underperforming: 100, preliminary: false, simulated: true } ] };

async function loadVendors() {
  let data = SAMPLE, live = false;
  try { if (API) { const r = await fetch(`${API}/api/vendors`); if (r.ok) { data = await r.json(); live = true; } } } catch (e) {}
  const rows = data.vendors || [];
  $("vendors").innerHTML = rows.length ? rows.map((v) => `<tr>
      <td class="name">${esc(v.vendor)}${v.preliminary ? '<span class="tag">preliminary</span>' : ""}${v.simulated ? '<span class="tag">simulated</span>' : ""}</td>
      <td>${v.systems}</td>
      <td><div class="bar" title="${fmt(v.score * 100, 0)}% of the healthy expectation"><i class="${v.score >= 0.95 ? "good" : ""}" style="width:${Math.min(v.score, 1.1) / 1.1 * 100}%"></i><b style="left:${0.95 / 1.1 * 100}%"></b></div>${fmt(v.score * 100, 0)}% of healthy</td>
      <td>${v.pct_underperforming}%</td></tr>`).join("") : '<tr><td colspan="4">No installers yet. Be the first: message Surya Sach on WhatsApp.</td></tr>';
  $("vendor-note").textContent = (live ? "" : "Sample data shown. ") +
    "100% of healthy means the roof makes exactly what the physics expects; the white line marks 95%. Installers with fewer than 3 systems are marked preliminary and ranked last. Simulated systems are demonstration data.";
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

$("go").addEventListener("click", check);
$("ask").addEventListener("keydown", (e) => { if (e.key === "Enter") check(); });
loadVendors();
check();
