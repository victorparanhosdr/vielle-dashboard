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
    window.location.replace(next === "/master" ? "/master" : "/");
  } catch (problem) {
    error.textContent = problem instanceof TypeError ? "Não foi possível conectar. Verifique sua conexão e tente novamente." : problem.message;
  } finally {
    submit.disabled = false;
    submit.textContent = "Entrar";
  }
});
