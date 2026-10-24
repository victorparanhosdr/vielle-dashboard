"use strict";
(() => {
  const reset = document.body.dataset.recoveryView === "reset";
  let token = reset ? new URLSearchParams(window.location.hash.slice(1)).get("token") || "" : "";
  // The email link carries the bearer in a fragment, not HTTP logs or page source.
  if (window.location.hash || window.location.search) window.history.replaceState(null, "", window.location.pathname);
  const form = document.getElementById("recoveryForm"), button = document.getElementById("recoverySubmit");
  const error = document.getElementById("recoveryError"), result = document.getElementById("recoveryResult");
  const label = button.textContent;
  if (reset && !/^[A-Za-z0-9_-]{43}$/.test(token)) {
    token = "";form.hidden = true;result.hidden = false;
    document.getElementById("resultTitle").textContent = "Link indisponível";
    document.getElementById("recoveryMessage").textContent = "Abra o link recebido por e-mail ou solicite um novo.";
    return;
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();if (button.disabled) return;
    error.textContent = "";
    if (reset && form.elements.password.value !== form.elements.password_confirmation.value) {
      error.textContent = "As senhas não conferem.";return;
    }
    button.disabled = true;button.textContent = reset ? "Salvando..." : "Enviando...";
    try {
      const body = reset ? {token, password:form.elements.password.value, password_confirmation:form.elements.password_confirmation.value} : {email:form.elements.email.value};
      const response = await fetch(reset ? "/api/auth/password/reset" : "/api/auth/password/forgot", {
        method:"POST",credentials:"same-origin",referrerPolicy:"no-referrer",
        headers:{"Content-Type":"application/json","X-DOC4DOCS-Request":"1"},body:JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || "Não foi possível concluir agora.");
      form.reset();form.hidden = true;result.hidden = false;
      document.getElementById("recoveryMessage").textContent = reset ? "Sua senha foi atualizada e as sessões anteriores foram encerradas. Entre novamente para continuar." : data.message;
      token = "";result.focus();
    } catch (problem) {
      error.textContent = problem instanceof TypeError ? "Não foi possível conectar. Tente novamente." : problem.message;
    } finally {
      button.disabled = false;button.textContent = label;
    }
  });
})();
