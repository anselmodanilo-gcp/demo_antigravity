"use strict";
// Painel de leads. Todo conteúdo dinâmico usa textContent (sem innerHTML) para evitar XSS.

const $ = (id) => document.getElementById(id);
let selected = null;

async function api(path) {
  const res = await fetch(path, { credentials: "same-origin", headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}

function fmtDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

const PRAZO = { ate_3_meses: "até 3 meses", de_3_a_6_meses: "3–6 meses", mais_de_6_meses: "+6 meses", so_pesquisando: "pesquisando" };
const STATUS = { novo: "novo", em_qualificacao: "qualificando", atendimento_humano: "com corretor", descartado: "opt-out" };

async function loadSummary() {
  const s = await api("/api/summary");
  $("k-total").textContent = s.total;
  $("k-quente").textContent = s.por_temperatura.QUENTE || 0;
  $("k-morno").textContent = s.por_temperatura.MORNO || 0;
  $("k-frio").textContent = s.por_temperatura.FRIO || 0;
  $("k-visitas").textContent = s.visitas_agendadas;
  $("k-corretor").textContent = s.corretor_avisado;
}

async function loadLeads() {
  const leads = await api("/api/leads");
  const body = $("leads");
  body.replaceChildren();
  $("empty").classList.toggle("hidden", leads.length > 0);
  for (const lead of leads) {
    const tr = el("tr");
    tr.tabIndex = 0;
    if (lead.lead_id === selected) tr.classList.add("selected");
    const who = el("td");
    who.append(el("strong", lead.nome), el("div", lead.telefone, "muted"));
    const score = el("td");
    score.append(el("span", `${lead.score} · ${lead.temperatura}`, `badge ${lead.temperatura.toLowerCase()}`));
    tr.append(
      who,
      el("td", lead.canal),
      score,
      el("td", lead.tipologia || "—"),
      el("td", PRAZO[lead.prazo_compra] || "—"),
      el("td", fmtDate(lead.visita)),
      el("td", STATUS[lead.status] || lead.status),
    );
    const open = () => selectLead(lead);
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
    body.append(tr);
  }
}

async function selectLead(lead) {
  selected = lead.lead_id;
  $("conv-title").textContent = `${lead.nome} · score ${lead.score} (${lead.temperatura})`;
  $("conv-summary").textContent = lead.resumo || "";
  $("conv-summary").classList.toggle("hidden", !lead.resumo);
  await loadConversation();
  await loadLeads();
}

async function loadConversation() {
  if (!selected) return;
  const msgs = await api(`/api/leads/${encodeURIComponent(selected)}/messages`);
  const box = $("conversation");
  box.replaceChildren();
  for (const m of msgs) {
    const bubble = el("div", null, `bubble ${m.role === "user" ? "in" : "out"}`);
    bubble.append(el("div", m.kind === "audio" ? `🎤 ${m.text}` : m.text), el("small", fmtDate(m.ts), "muted"));
    box.append(bubble);
  }
  box.scrollTop = box.scrollHeight;
}

async function loadNotifications() {
  const notes = await api("/api/notifications");
  const list = $("notifications");
  list.replaceChildren();
  if (!notes.length) list.append(el("li", "Nenhum aviso enviado ainda.", "muted"));
  for (const n of notes.slice(0, 10)) {
    const li = el("li");
    li.append(el("span", `${n.temperature} · ${n.score}`, `badge ${String(n.temperature).toLowerCase()}`), el("span", ` ${n.reason} — ${fmtDate(n.ts)}`));
    list.append(li);
  }
}

async function refresh() {
  try {
    await Promise.all([loadSummary(), loadLeads(), loadNotifications(), loadConversation()]);
    $("updated").textContent = `atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
  } catch (err) {
    $("updated").textContent = `erro ao atualizar (${err.message})`;
  }
}

api("/api/me").then((me) => { $("me").textContent = me.email; }).catch(() => {});
refresh();
setInterval(refresh, 4000);
