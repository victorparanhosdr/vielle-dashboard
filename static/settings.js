const master = {
  user: localStorage.getItem("vielle_master_user") || "master",
  password: "",
};

document.getElementById("masterUser").value = master.user;

function showSettingsStatus(message, isError = false) {
  const el = document.getElementById("settingsStatus");
  el.textContent = message;
  el.classList.toggle("visible", Boolean(message));
  el.classList.toggle("error", isError);
}

function headers() {
  return {
    "Content-Type": "application/json",
    "X-Master-User": master.user,
    "X-Master-Password": master.password,
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...headers(),
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "Não foi possível concluir a ação.");
  }
  return data;
}

function fillSettings(data) {
  const config = data.config || {};
  document.querySelectorAll("[data-config]").forEach(input => {
    const key = input.dataset.config;
    const item = config[key] || {};
    if (input.dataset.secret === "true") {
      input.value = "";
      const mask = document.querySelector(`[data-mask-for="${key}"]`);
      if (mask) mask.textContent = item.configured ? `Atual: ${item.masked || "configurado"}` : "Ainda não configurado";
      return;
    }
    input.value = item.value || "";
  });
}

function collectSettings() {
  const values = {};
  document.querySelectorAll("[data-config]").forEach(input => {
    const key = input.dataset.config;
    if (input.dataset.secret === "true" && !input.value.trim()) return;
    values[key] = input.value.trim();
  });
  return values;
}

async function unlock() {
  master.user = document.getElementById("masterUser").value.trim() || "master";
  master.password = document.getElementById("masterPassword").value;
  localStorage.setItem("vielle_master_user", master.user);
  showSettingsStatus("Validando acesso master...");
  const data = await api("/api/settings");
  fillSettings(data);
  document.getElementById("settingsArea").hidden = false;
  showSettingsStatus("Acesso liberado. Você já pode alterar integrações e senhas.");
}

async function saveSettings() {
  showSettingsStatus("Salvando configurações...");
  const data = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({ values: collectSettings() }),
  });
  fillSettings(data);
  showSettingsStatus("Configurações salvas com sucesso.");
}

async function loadEverything(reset = false) {
  const message = reset
    ? "Limpando dados locais e carregando todo o histórico pelas APIs..."
    : "Carregando todo o histórico pelas APIs...";
  showSettingsStatus(message);
  const data = await api("/api/sync-all", {
    method: "POST",
    body: JSON.stringify({
      historical: true,
      reset_data: reset,
      reset_oauth: reset,
    }),
  });
  const kommo = data.kommo?.ok ? "Kommo atualizado" : `Kommo: ${data.kommo?.error || "não atualizado"}`;
  const clinica = data.clinica_experts?.ok
    ? "Clínica Experts atualizado"
    : `Clínica Experts: ${data.clinica_experts?.error || "não atualizado"}`;
  showSettingsStatus(`${kommo}. ${clinica}.`);
}

document.getElementById("unlockSettings").addEventListener("click", () => {
  unlock().catch(error => showSettingsStatus(error.message, true));
});

document.getElementById("saveSettings").addEventListener("click", () => {
  saveSettings().catch(error => showSettingsStatus(error.message, true));
});

document.getElementById("loadEverything").addEventListener("click", () => {
  loadEverything(false).catch(error => showSettingsStatus(error.message, true));
});

document.getElementById("switchAccount").addEventListener("click", () => {
  const ok = confirm("Isso vai limpar os dados sincronizados locais e buscar tudo de novo pelas APIs configuradas. Confirma?");
  if (!ok) return;
  loadEverything(true).catch(error => showSettingsStatus(error.message, true));
});

document.getElementById("masterPassword").addEventListener("keydown", event => {
  if (event.key === "Enter") {
    unlock().catch(error => showSettingsStatus(error.message, true));
  }
});
