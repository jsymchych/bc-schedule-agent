"""One-screen local demo UI: four inputs, week grid, audit drawer, OT-gated downloads.

stdlib only. Hours/sales/availability/time-off path by default; optional local
Ollama is not required for the smoke gate.

  python3 -m bc_schedule_agent.demo_app
  # open http://127.0.0.1:8765/
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bc_schedule_agent.demo import DemoSession, SCENARIO_IDS, ensure_fixture_files
from bc_schedule_agent.export import ExportBlocked
from bc_schedule_agent.gates import GateError

SESSION = DemoSession()


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>BC schedule agent — local demo</title>
<style>
  :root {
    --ink: #1a2421;
    --paper: #f3efe6;
    --panel: #fffdf8;
    --line: #c9c0b0;
    --accent: #0f5c4c;
    --warn: #8a3b12;
    --muted: #5c655f;
    --grid: #e4ddd0;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
    color: var(--ink);
    background:
      radial-gradient(ellipse at 10% 0%, #e7f0eb 0%, transparent 45%),
      radial-gradient(ellipse at 90% 10%, #f0e6d8 0%, transparent 40%),
      var(--paper);
    min-height: 100vh;
  }
  header {
    padding: 1.25rem 1.5rem 0.75rem;
    border-bottom: 1px solid var(--line);
  }
  header h1 {
    margin: 0;
    font-size: 1.55rem;
    letter-spacing: 0.02em;
    color: var(--accent);
  }
  header p {
    margin: 0.35rem 0 0;
    color: var(--muted);
    font-size: 0.95rem;
  }
  main {
    display: grid;
    grid-template-columns: minmax(0, 1.4fr) minmax(280px, 0.9fr);
    gap: 1rem;
    padding: 1rem 1.5rem 2rem;
  }
  @media (max-width: 900px) {
    main { grid-template-columns: 1fr; }
  }
  .panel {
    background: var(--panel);
    border: 1px solid var(--line);
    padding: 1rem;
  }
  label { display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 0.25rem; }
  input[type="text"], select, textarea {
    width: 100%;
    font: inherit;
    padding: 0.45rem 0.55rem;
    border: 1px solid var(--line);
    background: #fff;
    margin-bottom: 0.65rem;
  }
  textarea { min-height: 3.2rem; resize: vertical; }
  .row { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
  button {
    font: inherit;
    border: 1px solid var(--accent);
    background: var(--accent);
    color: #f7fff9;
    padding: 0.45rem 0.85rem;
    cursor: pointer;
  }
  button.secondary {
    background: transparent;
    color: var(--accent);
  }
  button.warn {
    border-color: var(--warn);
    background: transparent;
    color: var(--warn);
  }
  button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
  .status {
    margin: 0.75rem 0;
    font-size: 0.9rem;
    color: var(--muted);
  }
  .status.warn { color: var(--warn); }
  .status.ok { color: var(--accent); }
  .inputs {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.55rem;
    margin: 0.5rem 0 0.85rem;
  }
  @media (max-width: 700px) {
    .inputs { grid-template-columns: 1fr; }
  }
  .input-card {
    border: 1px solid var(--grid);
    padding: 0.45rem 0.55rem;
    background: #faf7f0;
    font-size: 0.78rem;
  }
  .input-card h3 {
    margin: 0 0 0.25rem;
    font-size: 0.82rem;
    color: var(--accent);
  }
  .input-card pre {
    margin: 0;
    white-space: pre-wrap;
    font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
    color: var(--ink);
    max-height: 5.5rem;
    overflow: auto;
  }
  table.week {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
    margin-top: 0.5rem;
  }
  table.week th, table.week td {
    border: 1px solid var(--grid);
    padding: 0.4rem 0.45rem;
    text-align: left;
    vertical-align: top;
  }
  table.week th { background: #ebe4d6; font-weight: 600; }
  .cell-empty { color: #9aa39c; }
  .ot-list { margin: 0.75rem 0 0; padding: 0; list-style: none; }
  .ot-list li {
    border-left: 3px solid var(--warn);
    padding: 0.35rem 0.55rem;
    margin-bottom: 0.35rem;
    background: #fff6ef;
    font-size: 0.88rem;
  }
  #drawer {
    max-height: 70vh;
    overflow: auto;
    font-size: 0.88rem;
    line-height: 1.35;
  }
  #drawer ol { margin: 0; padding-left: 1.2rem; }
  #drawer li { margin-bottom: 0.45rem; }
  footer {
    padding: 0 1.5rem 1.5rem;
    font-size: 0.8rem;
    color: var(--muted);
  }
  .gate-fields {
    display: grid;
    grid-template-columns: 1fr 1.4fr;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
  }
  @media (max-width: 700px) {
    .gate-fields { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<header>
  <h1>BC schedule agent</h1>
  <p>Local proof — four inputs in, ESA-compliant week out. Synthetic names only. Decision support under the Employment Standards Act, not legal advice.</p>
</header>
<main>
  <section class="panel">
    <label for="scenario">Scenario</label>
    <select id="scenario">
      <option value="busy_week_zero_ot">Busy week — zero OT</option>
      <option value="peak_needs_ot">Peak needs OT</option>
      <option value="time_off_gate">Time-off gate</option>
      <option value="bad_s37_packet">Bad s.37 packet</option>
    </select>
    <label for="ask">Ask for a week</label>
    <textarea id="ask" placeholder="e.g. Take Mon–Fri hours and steady sales with Sam and Jordan available"></textarea>
    <div class="row">
      <button id="btn-run" type="button">Draft week</button>
      <button id="btn-download" type="button" disabled>Download PDF / XLSX / audit.json</button>
    </div>
    <h2 style="font-size:1.05rem;margin:1rem 0 0.35rem;">Four inputs</h2>
    <div id="inputs" class="inputs"><p class="cell-empty">Draft a scenario to show hours, sales, availability, and time-off.</p></div>
    <h2 style="font-size:1.05rem;margin:1rem 0 0.35rem;">OT human gate</h2>
    <div class="gate-fields">
      <div>
        <label for="human-name">Named human</label>
        <input id="human-name" type="text" placeholder="Alex Rivera" value=""/>
      </div>
      <div>
        <label for="ot-reason">Reason (required to approve)</label>
        <input id="ot-reason" type="text" placeholder="Why OT is unavoidable"/>
      </div>
    </div>
    <div class="row">
      <button id="btn-approve" class="secondary" type="button" disabled>Approve pending OT</button>
      <button id="btn-refuse" class="warn" type="button" disabled>Refuse OT</button>
    </div>
    <p id="status" class="status">Load a scenario or type an ask, then draft.</p>
    <h2 style="font-size:1.05rem;margin:1rem 0 0.35rem;">Week grid</h2>
    <div id="grid"></div>
    <ul id="ot" class="ot-list"></ul>
  </section>
  <aside class="panel">
    <h2 style="font-size:1.05rem;margin:0 0 0.5rem;">Audit drawer</h2>
    <div id="drawer"><p class="cell-empty">No events yet.</p></div>
  </aside>
</main>
<footer>
  Statute: https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/96113_01
  · Downloads stay off while any OT line is PENDING_APPROVAL or a rule_refuse is on the chain.
  · Agent cannot approve itself. Who approved OT and why is on the chain.
</footer>
<script>
const $ = (id) => document.getElementById(id);

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function renderInputs(inputs) {
  const box = $("inputs");
  if (!inputs || !Object.keys(inputs).length) {
    box.innerHTML = "<p class='cell-empty'>Draft a scenario to show hours, sales, availability, and time-off.</p>";
    return;
  }
  const order = ["hours_of_operation", "sales_projections", "availability", "time_off"];
  box.innerHTML = order.map((key) => {
    const item = inputs[key] || {};
    const label = item.label || key;
    const preview = (item.preview || "").replace(/</g, "&lt;");
    const rows = item.rows != null ? item.rows + " rows" : "";
    return `<div class="input-card"><h3>${label} <span style="color:var(--muted);font-weight:400">${rows}</span></h3><pre>${preview}</pre></div>`;
  }).join("");
}

function render(state) {
  const status = $("status");
  if (state.last_error) {
    status.textContent = state.last_error;
    status.className = "status warn";
  } else if (state.replay_sentence) {
    status.textContent = state.replay_sentence;
    status.className = "status ok";
  } else if (state.scenario_id) {
    const pend = state.pending_ot_count || 0;
    status.textContent = pend
      ? `Draft ready — ${pend} OT line(s) PENDING_APPROVAL. Downloads disabled until a named human records a reason (or refuses).`
      : (state.download_enabled
          ? "Draft clean — downloads enabled."
          : "Draft has refuses — downloads disabled.");
    status.className = pend ? "status warn" : "status ok";
  }

  $("btn-download").disabled = !state.download_enabled;
  const hasPending = state.pending_ot_count > 0;
  $("btn-approve").disabled = !hasPending;
  $("btn-refuse").disabled = !hasPending;
  if (state.scenario_id) $("scenario").value = state.scenario_id;
  if (state.ask) $("ask").value = state.ask;
  renderInputs(state.inputs);

  const byDate = {};
  (state.week_days || []).forEach((d) => { byDate[d] = []; });
  (state.placed || []).forEach((p) => {
    (byDate[p.date] = byDate[p.date] || []).push(p);
  });
  let html = "<table class='week'><thead><tr><th>Date</th><th>Shifts</th></tr></thead><tbody>";
  (state.week_days || []).forEach((d) => {
    const cells = (byDate[d] || []).map(
      (p) => `${p.employee} ${p.start}–${p.end} (${p.worked_hours}h)`
    );
    html += `<tr><td>${d}</td><td>${cells.length ? cells.join("<br/>") : "<span class='cell-empty'>—</span>"}</td></tr>`;
  });
  html += "</tbody></table>";
  $("grid").innerHTML = html;

  const ot = $("ot");
  ot.innerHTML = "";
  (state.ot_proposals || []).forEach((o) => {
    const li = document.createElement("li");
    const who = o.decided_by ? ` · ${o.decided_by}` : "";
    const why = o.reason ? ` · ${o.reason}` : "";
    li.textContent = `${o.employee} ${o.date}: ${o.hours}h @ ${o.multiplier}x (s.${o.section}) — ${o.status}${who}${why}`;
    ot.appendChild(li);
  });

  const drawer = $("drawer");
  const lines = state.audit_drawer || [];
  if (!lines.length) {
    drawer.innerHTML = "<p class='cell-empty'>No events yet.</p>";
  } else {
    drawer.innerHTML = "<ol>" + lines.map((s) => `<li>${s}</li>`).join("") + "</ol>";
  }
}

$("btn-run").onclick = async () => {
  try {
    const ask = $("ask").value.trim();
    const body = ask
      ? { ask }
      : { scenario_id: $("scenario").value };
    render(await api("/api/run", body));
  } catch (e) {
    $("status").textContent = String(e.message || e);
    $("status").className = "status warn";
  }
};

$("btn-approve").onclick = async () => {
  try {
    const human_name = $("human-name").value.trim();
    const reason = $("ot-reason").value.trim();
    if (!human_name) throw new Error("named human required");
    if (!reason) throw new Error("approve requires a non-empty reason");
    render(await api("/api/approve-ot", { human_name, reason }));
  } catch (e) {
    $("status").textContent = String(e.message || e);
    $("status").className = "status warn";
  }
};

$("btn-refuse").onclick = async () => {
  try {
    const human_name = $("human-name").value.trim() || "Alex Rivera";
    render(await api("/api/refuse-ot", { human_name }));
  } catch (e) {
    $("status").textContent = String(e.message || e);
    $("status").className = "status warn";
  }
};

$("btn-download").onclick = async () => {
  try {
    const state = await api("/api/download", {});
    render(state);
    await Promise.all([
      browserDownload("/api/exhibit/pdf"),
      browserDownload("/api/exhibit/xlsx"),
      browserDownload("/api/exhibit/audit.json"),
    ]);
    if (state.replay_sentence) {
      $("status").textContent = state.replay_sentence + " PDF, XLSX, and audit.json saved to Downloads.";
      $("status").className = "status ok";
    }
  } catch (e) {
    $("status").textContent = String(e.message || e);
    $("status").className = "status warn";
  }
};

async function browserDownload(path) {
  const res = await fetch(path);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || ("download failed: " + res.status));
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const match = /filename="([^"]+)"/.exec(cd);
  const name = match ? match[1] : path.split("/").pop();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

$("scenario").onchange = () => { $("ask").value = ""; };

fetch("/api/state").then((r) => r.json()).then(render).catch(() => {});
</script>
</body>
</html>
"""


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "BCScheduleDemo/0.2"

    def log_message(self, fmt: str, *args: Any) -> None:
        if args and isinstance(args[0], str) and args[0].startswith("code 5"):
            super().log_message(fmt, *args)

    def _send(self, code: int, payload: dict[str, Any] | str, *, content_type: str) -> None:
        raw = payload if isinstance(payload, bytes) else (
            payload.encode("utf-8") if isinstance(payload, str)
            else json.dumps(payload).encode("utf-8")
        )
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        self._send(code, payload, content_type="application/json; charset=utf-8")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send(200, PAGE, content_type="text/html; charset=utf-8")
            return
        if path == "/api/state":
            self._json(200, SESSION.to_state())
            return
        if path in {"/api/exhibit/pdf", "/api/exhibit/xlsx", "/api/exhibit/audit.json"}:
            self._serve_exhibit(path.rsplit("/", 1)[-1])
            return
        self._json(404, {"error": "not found"})

    def _serve_exhibit(self, kind: str) -> None:
        """Serve the last issued exhibit bytes so the browser can download them."""
        paths = SESSION.exhibit_paths or {}
        key = {"pdf": "pdf", "xlsx": "xlsx", "audit.json": "audit_json"}.get(kind)
        if key is None:
            self._json(404, {"error": f"unknown exhibit: {kind}"})
            return
        path_str = paths.get(key)
        if not path_str:
            self._json(409, {"error": "no exhibit yet — click Download first"})
            return
        file_path = Path(path_str)
        if not file_path.is_file():
            self._json(404, {"error": f"exhibit missing on disk: {file_path}"})
            return
        raw = file_path.read_bytes()
        content_types = {
            "pdf": "application/pdf",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "audit.json": "application/json; charset=utf-8",
        }
        self.send_response(200)
        self.send_header("Content-Type", content_types[kind])
        self.send_header("Content-Length", str(len(raw)))
        self.send_header(
            "Content-Disposition", f'attachment; filename="{file_path.name}"'
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return

        try:
            if path == "/api/run":
                ask = str(body.get("ask") or "").strip()
                scenario_id = str(body.get("scenario_id") or "").strip()
                if ask:
                    state = SESSION.run_ask(ask)
                elif scenario_id:
                    state = SESSION.run_scenario(scenario_id)
                else:
                    state = SESSION.run_scenario("busy_week_zero_ot")
                self._json(200, state)
                return
            if path == "/api/approve-ot":
                name = str(body.get("human_name") or "").strip()
                reason = str(body.get("reason") or "").strip()
                if not name:
                    raise GateError("named human required")
                if not reason:
                    raise GateError("approve_ot requires a non-empty reason")
                self._json(
                    200,
                    SESSION.approve_pending_ot(human_name=name, reason=reason),
                )
                return
            if path == "/api/refuse-ot":
                name = str(body.get("human_name") or "").strip()
                if not name:
                    raise GateError("named human required")
                self._json(200, SESSION.refuse_pending_ot(human_name=name))
                return
            if path == "/api/download":
                self._json(200, SESSION.write_downloads())
                return
            self._json(404, {"error": "not found"})
        except (ExportBlocked, GateError, RuntimeError, ValueError) as exc:
            SESSION.last_error = str(exc)
            self._json(409, {"error": str(exc), **SESSION.to_state()})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BC schedule agent local demo UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    ensure_fixture_files()
    httpd = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    print(
        f"BC schedule agent demo on http://{args.host}:{args.port}/ "
        f"(scenarios: {', '.join(SCENARIO_IDS)})"
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
