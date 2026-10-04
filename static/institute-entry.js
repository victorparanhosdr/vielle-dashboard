"use strict";
const WorkspaceLanding = (() => {
  function instituteUrl(institute) {
    const course = institute.courses?.[0];
    return course ? "/institutes.html?" + new URLSearchParams({institute:institute.key, course:course.key}) : "";
  }
  function initials(name) {
    return String(name || "").split(/\s+/).filter(word => !/^(instituto|dr\.?|dra\.?)$/i.test(word)).slice(0,2).map(word => word[0]).join("").toUpperCase() || "IN";
  }
  return {instituteUrl, initials};
})();
if (typeof module !== "undefined") module.exports = WorkspaceLanding;
if (typeof document !== "undefined") (() => {
  const tabs = [...document.querySelectorAll("[data-landing-area]")];
  const panels = {clinics:document.getElementById("clinicAccounts"), institutes:document.getElementById("instituteAccounts")};
  const links = document.querySelectorAll("[data-institute-entry]");
  const status = document.getElementById("instituteAccessState");
  const cards = document.getElementById("landingInstituteCards");
  const retry = document.getElementById("retryInstituteAccess");
  const initialQuery = new URLSearchParams(location.search);
  let request = 0, session = null;

  function selectArea(area, updateUrl=false) {
    if (!panels[area]) return;
    tabs.forEach(tab => {
      const active = tab.dataset.landingArea === area;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    Object.entries(panels).forEach(([key,panel]) => { panel.hidden = key !== area; });
    if (updateUrl) {
      const target = "/?" + new URLSearchParams({area});
      if (location.pathname + location.search !== target) history.pushState(null, "", target);
    }
  }
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function renderInstitutes(institutes) {
    if (!cards) return;
    cards.replaceChildren(...institutes.map(institute => {
      const courses = institute.courses || [];
      const card = element("article", undefined, "clinicAccessCard institute");
      card.dataset.instituteCard = institute.key;
      const header = element("div");
      header.append(element("span", WorkspaceLanding.initials(institute.name)), element("small", courses.length + (courses.length === 1 ? " curso" : " cursos")));
      card.append(header, element("h2", institute.name), element("p", courses.map(course => course.name).join(" · ")));
      const url = WorkspaceLanding.instituteUrl(institute);
      if (url) {
        const link = element("a", "Acessar instituto", "buttonLink");
        link.href = url;
        link.dataset.instituteSelect = institute.key;
        card.append(link);
      } else card.append(element("p", "Sem cursos cadastrados."));
      return card;
    }));
    status.textContent = institutes.length ? "" : "Nenhum instituto liberado para seu usuário. Fale com o Master.";
  }
  async function loadInstitutes() {
    const current = ++request;
    if (retry) retry.hidden = true;
    try {
      const response = await fetch("/api/institutes");
      const data = await response.json();
      if (current !== request) return;
      if (!response.ok || !data.ok) throw new Error("Não foi possível carregar seus institutos. Tente novamente.");
      const institutes = data.institutes || [];
      links.forEach(link => { link.hidden = !institutes.length; });
      renderInstitutes(institutes);
      const currentQuery = new URLSearchParams(location.search);
      if (institutes.length && Array.isArray(session?.clinics) && !session.clinics.length && !currentQuery.has("clinic") && !currentQuery.has("area")) {
        selectArea("institutes", true);
      }
    } catch (error) {
      if (current !== request) return;
      links.forEach(link => { link.hidden = true; });
      cards?.replaceChildren();
      if (status) status.textContent = error.message;
      if (retry) retry.hidden = false;
    }
  }
  tabs.forEach((tab,index) => {
    tab.addEventListener("click", () => selectArea(tab.dataset.landingArea, true));
    tab.addEventListener("keydown", event => {
      const offset = {ArrowRight:1, ArrowLeft:-1, Home:-index, End:tabs.length-1-index}[event.key];
      if (offset === undefined) return;
      event.preventDefault();
      const next = tabs[(index + offset + tabs.length) % tabs.length];
      selectArea(next.dataset.landingArea, true);
      next.focus();
    });
  });
  retry?.addEventListener("click", loadInstitutes);
  window.addEventListener("popstate", () => selectArea(new URLSearchParams(location.search).get("area") === "institutes" ? "institutes" : "clinics"));
  window.addEventListener("doc4docs-session", event => { session = event.detail; loadInstitutes(); });
  selectArea(initialQuery.get("area") === "institutes" ? "institutes" : "clinics");
})();
