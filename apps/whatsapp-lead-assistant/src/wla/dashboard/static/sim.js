"use strict";
// Simulador: publica no mesmo tópico do webhook e acompanha a resposta pelo Firestore.

const $ = (id) => document.getElementById(id);
const KEY = "wla-sim-session";

function newSession() {
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  const id = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  sessionStorage.setItem(KEY, id);
  return id;
}

let session = sessionStorage.getItem(KEY) || newSession();
let leadId = null;
let pending = false;

function bubble(text, role) {
  const div = document.createElement("div");
  div.className = `bubble ${role === "user" ? "out" : "in"}`;
  div.textContent = text;
  return div;
}

async function poll() {
  if (!leadId) return;
  const res = await fetch(`/api/leads/${encodeURIComponent(leadId)}/messages`, { credentials: "same-origin" });
  if (!res.ok) return;
  const msgs = await res.json();
  const chat = $("chat");
  chat.replaceChildren(...msgs.map((m) => bubble(m.text, m.role)));
  if (pending && msgs.length && msgs[msgs.length - 1].role !== "user") pending = false;
  if (pending) {
    const typing = bubble("digitando…", "assistant");
    typing.classList.add("typing");
    chat.append(typing);
  }
  chat.scrollTop = chat.scrollHeight;
}

$("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("text");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  $("chat").append(bubble(text, "user"));
  pending = true;
  const res = await fetch("/api/sim/send", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: session, text }),
  });
  if (!res.ok) {
    pending = false;
    $("chat").append(bubble(`Erro ao enviar (HTTP ${res.status}).`, "assistant"));
    return;
  }
  leadId = (await res.json()).lead_id;
});

$("reset").addEventListener("click", () => {
  session = newSession();
  leadId = null;
  pending = false;
  $("chat").replaceChildren();
});

fetch("/api/me", { credentials: "same-origin" })
  .then((r) => r.json())
  .then((me) => { $("me").textContent = me.email; })
  .catch(() => {});
setInterval(() => poll().catch(() => {}), 1500);
