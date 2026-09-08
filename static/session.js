(() => {
  "use strict";
  const originalFetch = window.fetch.bind(window);
  const channel = "BroadcastChannel" in window ? new BroadcastChannel("doc4docs-session") : null;
  let redirecting = false;
  function signedOut() {
    if (redirecting) return;
    redirecting = true;
    Object.keys(sessionStorage).filter(key => key.startsWith("clinicAccess:")).forEach(key => sessionStorage.removeItem(key));
    localStorage.removeItem("selectedClinic");
    window.location.replace("/login");
  }
  window.fetch = async (input, init = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, window.location.href);
    if (url.origin !== window.location.origin || !url.pathname.startsWith("/api/")) return originalFetch(input, init);
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    headers.set("X-DOC4DOCS-Request", "1");
    const response = await originalFetch(input, {...init, headers});
    if (response.status === 401) {
      const data = await response.clone().json().catch(() => ({}));
      if (data.code === "session_required") signedOut();
    }
    if (response.status === 403) {
      const data = await response.clone().json().catch(() => ({}));
      if (data.code === "clinic_forbidden") window.dispatchEvent(new Event("doc4docs-clinic-forbidden"));
      if (data.code === "permission_denied") window.dispatchEvent(new Event("doc4docs-permission-denied"));
    }
    return response;
  };
  async function checkSession() {
    try {
      const response = await window.fetch("/api/auth/me");
      if (!response.ok) return;
      const data = await response.json();
      document.querySelectorAll("[data-session-user]").forEach(element => { element.textContent = data.user.nome; });
      document.querySelectorAll("[data-master-only]").forEach(element => { element.hidden = !data.user.is_master; });
      window.dispatchEvent(new CustomEvent("doc4docs-session", {detail: data}));
    } catch (_) { /* A temporary network outage is not a logout. */ }
  }
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-logout]").forEach(button => button.addEventListener("click", async () => {
      button.disabled = true;
      const status = document.getElementById("sessionStatus");
      try {
        const response = await window.fetch("/api/auth/logout", {method: "POST"});
        if (!response.ok) throw new Error();
        channel?.postMessage("logout");
        signedOut();
      } catch (_) {
        if (status) status.textContent = "Não foi possível sair. Tente novamente.";
        button.disabled = false;
      }
    }));
  });
  window.addEventListener("pageshow", checkSession);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkSession(); });
  if (channel) channel.onmessage = event => { if (event.data === "logout") signedOut(); };
})();
