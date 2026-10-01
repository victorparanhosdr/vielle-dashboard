(function (root) {
  'use strict';
  class ClinicRefreshController {
    constructor({ button, status, onComplete, getPeriod, canReload = () => true }) {
      this.button = button;
      this.label = button.querySelector('[data-refresh-label]');
      this.status = status;
      this.onComplete = onComplete;
      this.getPeriod = getPeriod;
      this.canReload = canReload;
      this.clinic = '';
      this.generation = 0;
      this.requestId = 0;
      this.timer = null;
      this.completed = new Map();
      button.addEventListener('click', () => this.request(true));
    }

    setClinic(clinic) {
      if (clinic === this.clinic) return;
      this.clinic = clinic;
      this.generation++;
      clearTimeout(this.timer);
      this.status.hidden = true;
      this.status.textContent = '';
      this.button.title = '';
      this.setBusy(false);
      if (clinic) this.request(false);
    }

    setBusy(busy) {
      this.busy = busy;
      this.button.disabled = busy || !this.clinic;
      this.button.setAttribute('aria-busy', String(busy));
      this.label.textContent = busy ? 'Atualizando...' : 'Atualizar tudo';
    }

    async request(start) {
      const clinic = this.clinic, generation = this.generation;
      if (!clinic || (start && this.busy)) return;
      const requestId = ++this.requestId;
      clearTimeout(this.timer);
      if (start) {
        this.setBusy(true);
        this.status.hidden = false;
        this.status.dataset.phase = 'running';
        this.status.textContent = 'Iniciando atualização das integrações...';
      }
      try {
        const response = await fetch(`/api/refresh?clinic=${encodeURIComponent(clinic)}`, start ? {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.getPeriod()),
        } : undefined);
        const data = await response.json();
        if (generation !== this.generation || clinic !== this.clinic || requestId !== this.requestId) return;
        if (!response.ok || !data.ok) throw new Error(data.error || 'Não foi possível consultar a atualização.');
        if (data.clinic !== clinic) throw new Error('Resposta de atualização inválida.');
        this.setBusy(Boolean(data.running));
        this.status.hidden = !data.message;
        this.status.textContent = data.message || '';
        this.status.dataset.phase = data.phase;
        this.button.title = '';
        if (data.running) {
          this.timer = setTimeout(() => this.request(false), 3000);
        } else {
          if (data.retry_after) {
            this.button.disabled = true;
            this.button.title = 'Atualização recente. Aguarde um momento para atualizar novamente.';
            this.timer = setTimeout(() => this.request(false), data.retry_after * 1000 + 100);
          }
          if (data.job_id && this.completed.get(clinic) !== data.job_id && this.canReload()) {
            this.completed.set(clinic, data.job_id);
            await this.onComplete(clinic);
          }
          if (generation !== this.generation || clinic !== this.clinic || requestId !== this.requestId) return;
          if (!data.retry_after) this.timer = setTimeout(() => this.request(false), 30000);
        }
      } catch (error) {
        if (generation !== this.generation || clinic !== this.clinic || requestId !== this.requestId) return;
        this.setBusy(false);
        this.status.hidden = false;
        this.status.dataset.phase = 'error';
        this.status.textContent = error.message || 'Falha de conexão. Tente atualizar novamente.';
        this.timer = setTimeout(() => this.request(false), 30000);
      }
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = ClinicRefreshController;
  else root.ClinicRefreshController = ClinicRefreshController;
})(typeof globalThis === 'undefined' ? this : globalThis);
