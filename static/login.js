"use strict";
const form = document.getElementById("loginForm");
const submit = document.getElementById("loginSubmit");
const error = document.getElementById("loginError");
form.addEventListener("submit", async event => {
  event.preventDefault();
  if (submit.disabled) return;
  submit.disabled = true;
  submit.textContent = "Entrando...";
  error.textContent = "";
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-DOC4DOCS-Request": "1"},
      body: JSON.stringify({login: form.elements.login.value, password: form.elements.password.value}),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Não foi possível entrar.");
    Object.keys(sessionStorage).filter(key => key.startsWith("clinicAccess:")).forEach(key => sessionStorage.removeItem(key));
    localStorage.removeItem("selectedClinic");
    form.elements.password.value = "";
    const next = new URLSearchParams(window.location.search).get("next");
    let destination = next === "/master" ? "/master" : "/";
    if (next?.startsWith("/body-evolution.html?")) {
      const target = new URL(next, window.location.origin);
      if (target.origin === window.location.origin && target.pathname === "/body-evolution.html") destination = target.pathname + target.search;
    }
    window.location.replace(destination);
  } catch (problem) {
    error.textContent = problem instanceof TypeError ? "Não foi possível conectar. Verifique sua conexão e tente novamente." : problem.message;
  } finally {
    submit.disabled = false;
    submit.textContent = "Entrar";
  }
});
