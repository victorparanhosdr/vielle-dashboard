"use strict";
window.addEventListener("doc4docs-session", async event => {
  const links = document.querySelectorAll("[data-institute-entry]");
  if (!links.length) return;
  try {
    const response = await fetch("/api/institutes");
    const data = await response.json();
    links.forEach(link => { link.hidden = !response.ok || !data.institutes?.length; });
    if (response.ok && data.institutes?.length && Array.isArray(event.detail.clinics)
        && !event.detail.clinics.length && !new URLSearchParams(location.search).has("clinic")) {
      location.replace("/institutos");
    }
  } catch (_) { links.forEach(link => { link.hidden = true; }); }
});
