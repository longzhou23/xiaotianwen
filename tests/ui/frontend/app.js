(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="xtw-csrf"]').content;
  let activeRun = null;
  const byId = (id) => document.getElementById(id);
  const show = (id, value) => { byId(id).textContent = JSON.stringify(value, null, 2); };
  const request = async (path, method = "GET", payload = null) => {
    const headers = { "Accept": "application/json" };
    if (method !== "GET") {
      headers["Content-Type"] = "application/json";
      headers["X-XTW-CSRF"] = csrf;
    }
    const response = await fetch(path, { method, headers, credentials: "same-origin", body: payload === null ? null : JSON.stringify(payload) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  };
  const refresh = async () => {
    show("runs", await request("/api/runs"));
    if (!activeRun) return;
    const base = `/api/runs/${encodeURIComponent(activeRun)}`;
    show("requests", await request(`${base}/requests`));
    show("outputs", await request(`${base}/outputs`));
    show("timeline", await request(`${base}/timeline`));
    show("compare", await request(`${base}/compare`));
  };
  byId("new-run").addEventListener("click", async () => {
    try {
      const body = await request("/api/runs", "POST", {});
      activeRun = body.run.run_id;
      byId("run-status").textContent = `Run ${activeRun} 已创建；${body.run.data_source}`;
      await refresh();
    } catch (error) { byId("run-status").textContent = String(error); }
  });
  byId("send").addEventListener("click", async () => {
    try {
      if (!activeRun) {
        const created = await request("/api/runs", "POST", {});
        activeRun = created.run.run_id;
      }
      const messages = byId("input").value.split(/\n\s*\n/).filter((text) => text.length > 0).map((text, index) => ({ text, at_ms: index * 1000 }));
      const images = byId("image").checked ? [{ id: "synthetic-ui-image-001", mime: "image/jpeg" }] : [];
      const body = await request(`/api/runs/${encodeURIComponent(activeRun)}/inputs`, "POST", { route: byId("route").value, provider: byId("provider").value, template: byId("template").value, messages, images });
      byId("run-status").textContent = `${body.run.status} · capture=${body.capture_mode} · ${body.note}`;
      await refresh();
    } catch (error) { byId("run-status").textContent = String(error); }
  });
  byId("cancel").addEventListener("click", async () => {
    if (!activeRun) return;
    try {
      const body = await request(`/api/runs/${encodeURIComponent(activeRun)}/cancel`, "POST", {});
      byId("run-status").textContent = `Run ${body.run.run_id} 已取消。`;
      await refresh();
    } catch (error) { byId("run-status").textContent = String(error); }
  });
  refresh().catch((error) => { byId("run-status").textContent = String(error); });
})();
