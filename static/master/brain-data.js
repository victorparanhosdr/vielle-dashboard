(function (root) {
  "use strict";
  const FOCUSES = {general: "Visão geral", sync: "Sincronizações", quality: "Qualidade dos dados", architecture: "Arquitetura"};
  const STATES = {ok: "Último registro concluído", error: "Falha registrada", warning: "Atenção", stale: "Registro há mais de 24h", running: "Em execução", unknown: "Sem conclusão disponível", info: "Informação"};
  const finite = value => typeof value === "number" && Number.isFinite(value);

  function inventoryMetrics(file) {
    const measured = file.analysis_status === "measured";
    const unavailable = ["invalid", "unavailable"].includes(file.analysis_status);
    const missing = unavailable ? "Não disponíveis" : "Não medidas";
    return {
      functions: measured && Number.isSafeInteger(file.functions) && file.functions >= 0 ? String(file.functions) : missing,
      dependencies: measured ? (file.dependencies || []).join(", ") || "Nenhuma detectada" : missing,
      hash: file.hash || "Não disponível",
    };
  }

  function filterInventory(inventory, query = "") {
    const normalize = value => String(value ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    const terms = normalize(query).trim().split(/\s+/).filter(Boolean);
    const matches = value => terms.every(term => normalize(value).includes(term));
    const files = inventory?.files || [], routes = inventory?.endpoints || [];
    return {files: files.filter(file => matches([file.name, ...(file.dependencies || [])].join(" "))),
      routes: routes.filter(matches), totalFiles: files.length, totalRoutes: routes.length};
  }

  function syncSample(info) {
    const size = info.sample_size, completed = info.completed_in_sample, failures = info.failures_last_10;
    return {
      label: !finite(size) ? "Histórico não medido" : size === 0 ? "Nenhuma tentativa registrada" : size === 1 ? "Última tentativa observada" : `Últimas ${size} tentativas observadas`,
      failures: finite(completed) && completed > 0 && finite(failures) ? `${failures} de ${completed} concluída${completed === 1 ? "" : "s"}` : "Não medidas",
      inconsistent: finite(info.unclassified_in_sample) && info.unclassified_in_sample > 0 ? `${info.unclassified_in_sample} registro${info.unclassified_in_sample === 1 ? "" : "s"} sem dados consistentes` : null,
    };
  }

  function datasetDate(row, key) {
    if (row[key]) return row[key];
    const labels = {not_applicable: "Não aplicável", unavailable: "Não disponível", empty: "Sem data registrada", invalid: "Data inconsistente"};
    return labels[row.date_status] || "Não disponível";
  }

  function historyNotice(status) {
    if (status?.state === "unavailable") return "Histórico temporariamente indisponível. Os registros não foram alterados. Atualize o diagnóstico para tentar novamente; novas análises estão pausadas.";
    if (status?.state === "partial") {
      const count = Number.isInteger(status.skipped) && status.skipped > 0 ? status.skipped : null;
      return `${count === 1 ? "1 análise salva não pôde ser exibida" : count ? `${count} análises salvas não puderam ser exibidas` : "Parte do histórico não pôde ser exibida"}. Os registros originais foram preservados; a comparação utiliza somente as análises legíveis.`;
    }
    return "";
  }

  function compareCode(previous, current) {
    const complete = item => item?.inventory_status === "complete" && typeof item.fingerprint === "string" && Boolean(item.fingerprint.trim());
    return complete(previous) && complete(current) ? previous.fingerprint !== current.fingerprint : null;
  }

  function compareAnalyses(previous, current) {
    if (!previous || !current || !previous.clinic || previous.clinic !== current.clinic) return null;
    if (![previous.observed_at, current.observed_at].every(value => finite(value) && value > 0) || previous.observed_at > current.observed_at) return null;
    const changes = [];
    const before = new Map((previous.evidence || []).map(item => [item.id, item]));
    const after = new Map((current.evidence || []).map(item => [item.id, item]));
    for (const [id, item] of after) {
      const old = before.get(id);
      if (!old) {
        changes.push({kind: "measurement", id, label: "Nova medição disponível", before: null, after: item.message});
        continue;
      }
      if (old.state !== item.state) changes.push({kind: "state", id, label: "Estado observado", before: STATES[old.state] || old.state, after: STATES[item.state] || item.state});
      if (finite(old.count) && finite(item.count) && old.count !== item.count) changes.push({kind: "count", id, label: "Quantidade observada", before: old.count, after: item.count, delta: item.count - old.count});
      if (finite(old.last_success) && finite(item.last_success) && old.last_success !== item.last_success) changes.push({kind: "sync", id, label: "Último sucesso registrado", before: old.last_success, after: item.last_success});
    }
    for (const [id] of before) {
      if (!after.has(id)) changes.push({kind: "measurement", id, label: "Medição indisponível nesta leitura", before: "Disponível", after: null});
    }
    const oldDatasets = new Map((previous.diagnostic?.datasets || []).map(item => [item.table, item]));
    for (const row of current.diagnostic?.datasets || []) {
      const old = oldDatasets.get(row.table);
      if (old && finite(old.records) && finite(row.records) && old.records !== row.records) changes.push({kind: "dataset", id: row.table, label: "Total de registros na base", before: old.records, after: row.records, delta: row.records - old.records});
    }
    return {from: previous.observed_at, to: current.observed_at, changes,
      codeChanged: compareCode(previous, current),
      datasetComparisonAvailable: Array.isArray(previous.diagnostic?.datasets) && Array.isArray(current.diagnostic?.datasets)};
  }

  function technicalReport(snapshot, analysis) {
    if (!snapshot?.clinic || (analysis && analysis.clinic !== snapshot.clinic)) throw new Error("Clínica incompatível com o relatório.");
    return {
      format: "doc4docs-technical-report", version: 1, clinic: snapshot.clinic, clinic_name: snapshot.clinic_name,
      observed_at: snapshot.generated_at, code_fingerprint: snapshot.inventory.fingerprint,
      revision: snapshot.inventory.revision, limitations: snapshot.limitations,
      inventory_status: snapshot.inventory.status || null,
      database: snapshot.health.database, datasets: snapshot.health.datasets,
      integrations: Object.fromEntries(Object.entries(snapshot.health.integrations).map(([key, value]) => [key, {
        state: value.state, label: value.label, last_success: value.last_success,
        duration_seconds: value.duration_seconds, failures_last_10: value.failures_last_10,
        sample_size: value.sample_size, completed_in_sample: value.completed_in_sample,
        unclassified_in_sample: value.unclassified_in_sample,
      }])),
      evidence: snapshot.evidence,
      history_status: snapshot.history_status ? {state: snapshot.history_status.state, skipped: snapshot.history_status.skipped} : null,
      analysis: analysis ? {id: analysis.id, clinic: analysis.clinic, model: analysis.model, focus: analysis.focus,
        created_at: analysis.created_at, observed_at: analysis.observed_at, fingerprint: analysis.fingerprint,
        prompt_version: analysis.prompt_version,
        inventory_status: analysis.inventory_status,
        summary: analysis.summary, findings: analysis.findings, evidence: analysis.evidence} : null,
    };
  }
  const api = {FOCUSES, compareAnalyses, compareCode, technicalReport, syncSample, datasetDate, historyNotice, filterInventory, inventoryMetrics};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BrainData = api;
})(typeof window !== "undefined" ? window : this);
