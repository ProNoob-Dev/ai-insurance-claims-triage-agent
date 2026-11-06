/* Claims Triage dashboard — vanilla JS, talks to the FastAPI backend.
   No build step, no external dependencies. */

const API = "";

/* ---------- helpers ---------- */
const $ = (sel) => document.querySelector(sel);

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function inr(amount) {
  if (amount === null || amount === undefined) return "—";
  return "₹" + Number(amount).toLocaleString("en-IN");
}

function statusBadge(status) {
  const labels = {
    auto_approved: "Auto-approved",
    pending_review: "Pending review",
    approved: "Approved",
    denied: "Denied",
    info_requested: "Info requested",
    submitted: "Submitted",
    triaged: "Triaged",
  };
  const label = labels[status] || status;
  return `<span class="badge badge-${esc(status)}">${esc(label)}</span>`;
}

function decisionLabel(decision) {
  const labels = {
    auto_approve: "Auto-approve",
    route_to_adjuster: "Route to adjuster",
    propose_deny: "Propose deny",
  };
  return labels[decision] || decision || "—";
}

async function api(path, options) {
  const res = await fetch(API + path, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text.slice(0, 200)}`);
  }
  return res.json();
}

/* ---------- state ---------- */
let selectedClaimId = null;

/* ---------- metrics ---------- */
async function loadMetrics() {
  try {
    const m = await api("/metrics");
    $("#m-total").textContent = m.total_claims;
    $("#m-autonomous").textContent = `${Math.round(m.autonomous_rate * 100)}%`;
    $("#m-avg-ms").textContent = `${m.avg_triage_ms} ms`;
    $("#m-pending").textContent = m.pending_review;
  } catch (err) {
    console.error("metrics failed", err);
  }
}

async function loadServiceInfo() {
  try {
    const info = await api("/health");
    $("#llm-badge").textContent = `LLM: ${info.llm_provider || "?"}`;
  } catch (err) {
    $("#llm-badge").textContent = "LLM: ?";
  }
}

/* ---------- claims list + queue ---------- */
async function loadClaims() {
  const status = $("#filter-status").value;
  const type = $("#filter-type").value;
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (type) params.set("claim_type", type);
  const query = params.toString();
  let claims = [];
  try {
    claims = await api(`/claims${query ? "?" + query : ""}`);
  } catch (err) {
    $("#claims-body").innerHTML =
      `<tr><td colspan="7" class="muted">Failed to load claims: ${esc(err.message)}</td></tr>`;
    return;
  }
  const body = $("#claims-body");
  if (!claims.length) {
    body.innerHTML = `<tr><td colspan="7" class="muted">No claims yet — submit one above.</td></tr>`;
    return;
  }
  body.innerHTML = claims
    .map(
      (c) => `<tr data-id="${esc(c.id)}" class="${c.id === selectedClaimId ? "selected" : ""}">
        <td><span class="mono">${esc(c.id)}</span></td>
        <td>${esc(c.claim_type)}</td>
        <td>${inr(c.claimed_amount)}</td>
        <td>${esc(decisionLabel(c.decision))}</td>
        <td>${statusBadge(c.status)}</td>
        <td>${Number(c.anomaly_score).toFixed(2)}${c.requires_hitl ? " ⚠️" : ""}</td>
        <td>${esc(new Date(c.created_at).toLocaleString())}</td>
      </tr>`
    )
    .join("");
  body.querySelectorAll("tr[data-id]").forEach((row) => {
    row.addEventListener("click", () => selectClaim(row.dataset.id));
  });
}

async function loadQueue() {
  let pending = [];
  try {
    pending = await api("/claims?status=pending_review");
  } catch (err) {
    console.error("queue failed", err);
  }
  $("#queue-count").textContent = pending.length;
  const list = $("#queue-list");
  if (!pending.length) {
    list.innerHTML = `<li class="muted">Queue is clear — nothing needs a human right now.</li>`;
    return;
  }
  list.innerHTML = pending
    .map(
      (c) => `<li data-id="${esc(c.id)}">
        <span><span class="q-id">${esc(c.id)}</span><br>
        <span class="q-meta">${esc(c.claim_type)} · ${inr(c.claimed_amount)} ·
        ${esc(decisionLabel(c.decision))}</span></span>
        ${statusBadge(c.status)}
      </li>`
    )
    .join("");
  list.querySelectorAll("li[data-id]").forEach((li) => {
    li.addEventListener("click", () => selectClaim(li.dataset.id));
  });
}

/* ---------- claim detail ---------- */
async function selectClaim(id) {
  selectedClaimId = id;
  const card = $("#detail-card");
  card.hidden = false;
  $("#detail-title").textContent = `Claim ${id}`;
  $("#detail-body").innerHTML = `<p class="muted">Loading…</p>`;
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    const [claim, audit] = await Promise.all([
      api(`/claims/${id}`),
      api(`/audit?claim_id=${id}`),
    ]);
    renderDetail(claim, audit);
    loadClaims(); // refresh row highlight
  } catch (err) {
    $("#detail-body").innerHTML =
      `<p class="form-status err">Failed to load claim: ${esc(err.message)}</p>`;
  }
}

function renderDetail(claim, audit) {
  const ef = claim.extracted_fields || {};
  const citations = claim.citations || [];
  const trace = claim.trace || [];
  const flags = claim.anomaly_flags || [];

  const reviewBox =
    claim.status === "pending_review"
      ? `<div class="review-box">
          <strong>Human review required</strong> — ${esc(decisionLabel(claim.proposed_decision))} was proposed by the supervisor.
          <div class="row">
            <input id="reviewer-name" type="text" placeholder="Your name (reviewer)" value="adjuster-1">
            <input id="review-note" type="text" placeholder="Note (optional)">
            <button class="btn btn-approve" data-review="approve" type="button">✓ Approve</button>
            <button class="btn btn-danger" data-review="deny" type="button">✕ Deny</button>
            <button class="btn btn-warn" data-review="request_info" type="button">? Request info</button>
          </div>
          <p id="review-status" class="form-status" role="status"></p>
        </div>`
      : claim.reviewer
        ? `<p><strong>Reviewed by ${esc(claim.reviewer)}</strong>${claim.review_note ? ` — ${esc(claim.review_note)}` : ""}</p>`
        : "";

  $("#detail-body").innerHTML = `
    <p>
      ${statusBadge(claim.status)}
      <span class="badge badge-grey">${esc(claim.claim_type)}</span>
      ${claim.requires_hitl ? `<span class="badge badge-amber">Needs human</span>` : ""}
    </p>
    <div class="detail-grid">
      <div>
        <h3>Extracted fields</h3>
        <dl class="kv">
          <dt>Policy</dt><dd>${esc(claim.policy_number)}</dd>
          <dt>Incident date</dt><dd>${esc(claim.incident_date || "—")}</dd>
          <dt>Claimed amount</dt><dd>${inr(claim.claimed_amount)}</dd>
          <dt>Coverage</dt><dd>${claim.covered === null ? "—" : claim.covered ? `Yes — ${esc(claim.coverage_clause_id || "")}` : "No"}</dd>
          <dt>Coverage reasoning</dt><dd>${esc(claim.coverage_reasoning || "—")}</dd>
          <dt>Decision</dt><dd>${esc(decisionLabel(claim.decision))}</dd>
          <dt>Anomaly score</dt><dd>${Number(claim.anomaly_score).toFixed(2)}</dd>
          <dt>Triage time</dt><dd>${claim.triage_duration_ms} ms</dd>
        </dl>
        <h3 style="margin-top:1rem">Incident</h3>
        <p>${esc(claim.incident_description)}</p>
        ${flags.length ? `<h3>Anomaly flags</h3><p>${flags.map((f) => `<span class="flag">${esc(f)}</span>`).join("")}</p>` : "<p class='muted'>No anomaly flags.</p>"}
      </div>
      <div>
        <h3>Policy citations</h3>
        ${citations.length
          ? citations.map((c) => `<div class="citation"><strong>${esc(c.clause_id)}</strong> — ${esc(c.clause_title)} <span class="muted">(score ${c.score})</span><br>${esc(c.excerpt)}</div>`).join("")
          : "<p class='muted'>No citations.</p>"}
        <h3 style="margin-top:1rem">Agent trace</h3>
        <ul class="trace">
          ${trace.map((t) => `<li><span class="agent">${esc(t.agent)}</span> · ${esc(t.action)}<br>${esc(t.detail)}</li>`).join("")}
        </ul>
      </div>
    </div>
    <h3 style="margin-top:1rem">Audit trail</h3>
    <ul class="audit">
      ${audit.length
        ? audit.map((a) => `<li><span class="actor">${esc(a.actor)}</span> — ${esc(a.action)}<br><span class="muted">${esc(a.detail)}</span></li>`).join("")
        : "<li class='muted'>No audit entries.</li>"}
    </ul>
    ${reviewBox}`;

  document.querySelectorAll("#detail-body [data-review]").forEach((btn) => {
    btn.addEventListener("click", () => submitReview(claim.id, btn.dataset.review));
  });
}

async function submitReview(claimId, decision) {
  const reviewer = ($("#reviewer-name") || {}).value || "reviewer";
  const note = ($("#review-note") || {}).value || "";
  const statusEl = $("#review-status");
  statusEl.textContent = "Submitting review…";
  statusEl.className = "form-status";
  try {
    await api(`/claims/${claimId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, reviewer, note }),
    });
    await refreshAll();
    await selectClaim(claimId);
  } catch (err) {
    statusEl.textContent = `Review failed: ${err.message}`;
    statusEl.className = "form-status err";
  }
}

/* ---------- submit form ---------- */
function readFilesAsText(files) {
  const readers = [...files].map(
    (file) =>
      new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve({ name: file.name, content: String(reader.result || "") });
        reader.onerror = () => resolve({ name: file.name, content: "" });
        reader.readAsText(file);
      })
  );
  return Promise.all(readers);
}

async function submitClaimForm(event) {
  event.preventDefault();
  const form = event.target;
  const statusEl = $("#form-status");
  statusEl.textContent = "Triaging…";
  statusEl.className = "form-status";

  const data = new FormData(form);
  const documents = [];
  const docName = (data.get("document_name") || "").trim();
  const docText = (data.get("document_text") || "").trim();
  if (docText) documents.push({ name: docName || "document.txt", content: docText });

  const files = $("#document-files").files;
  if (files && files.length) {
    const uploaded = await readFilesAsText(files);
    documents.push(...uploaded.filter((d) => d.content.trim()));
  }

  const payload = {
    claimant_name: data.get("claimant_name"),
    policy_number: data.get("policy_number"),
    policy_start_date: data.get("policy_start_date") || null,
    incident_date: data.get("incident_date") || null,
    incident_description: data.get("incident_description"),
    claimed_amount: Number(data.get("claimed_amount")),
    claim_type_hint: data.get("claim_type_hint") || null,
    documents,
    flags: [],
  };

  try {
    const claim = await api("/claims", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    statusEl.textContent =
      `Triaged: ${claim.id} → ${decisionLabel(claim.decision)} (${claim.status}).`;
    statusEl.className = "form-status ok";
    form.reset();
    await refreshAll();
    await selectClaim(claim.id);
  } catch (err) {
    statusEl.textContent = `Failed: ${err.message}`;
    statusEl.className = "form-status err";
  }
}

/* ---------- boot ---------- */
async function refreshAll() {
  await Promise.all([loadMetrics(), loadClaims(), loadQueue()]);
}

document.addEventListener("DOMContentLoaded", () => {
  loadServiceInfo();
  refreshAll();
  $("#refresh-btn").addEventListener("click", refreshAll);
  $("#claim-form").addEventListener("submit", submitClaimForm);
  $("#filter-status").addEventListener("change", loadClaims);
  $("#filter-type").addEventListener("change", loadClaims);
  $("#detail-close").addEventListener("click", () => {
    $("#detail-card").hidden = true;
    selectedClaimId = null;
    loadClaims();
  });
  setInterval(refreshAll, 30000); // refresh every 30s
});
