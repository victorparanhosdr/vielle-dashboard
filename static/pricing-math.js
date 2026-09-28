/* Pure pricing rules. Money is reconciled in integer cents; no clinic data. */
(function (root) {
  'use strict';
  const money = value => Math.round((value + Number.EPSILON) * 100);
  const bounded = (value, min, max) => Number.isFinite(value) && value >= min && value <= max;

  function parseNumber(value) {
    if (typeof value === 'number') return value;
    const text = String(value ?? '').trim().replace(/^R\$\s*/, '').replace(/\s/g, '');
    if (!text) return NaN;
    if (/^-?(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?$/.test(text)) {
      return Number(text.replace(/\./g, '').replace(',', '.'));
    }
    return /^-?\d+\.\d{1,4}$/.test(text) ? Number(text) : NaN;
  }

  function calculate(input) {
    const errors = {};
    const rules = {
      monthlyCost: [0, 1e9, 'Informe um custo mensal válido.'],
      days: [1, 31, 'Informe de 1 a 31 dias por mês.'],
      hours: [0.01, 24, 'Informe as horas de atendimento por dia (até 24).'],
      rooms: [1, 1000, 'Informe ao menos uma sala.'],
      occupancy: [0.01, 100, 'A ocupação deve ser maior que 0% e até 100%.'],
      duration: [0.01, 10080, 'Informe a duração total do procedimento em minutos.'],
      tax: [0, 100, 'Informe um imposto entre 0% e 100%.'],
      card: [0, 100, 'Informe uma taxa entre 0% e 100%.'],
      commission: [0, 100, 'Informe uma comissão entre 0% e 100%.'],
      margin: [0, 100, 'Informe uma margem entre 0% e 100%.'],
    };
    for (const [key, [min, max, message]] of Object.entries(rules)) {
      if (!bounded(input[key], min, max)) errors[key] = message;
    }
    for (const key of ['days', 'rooms']) {
      if (!Number.isInteger(input[key])) errors[key] = 'Informe um número inteiro.';
    }
    const materials = (input.materials || []).map((item, index) => {
      const key = `material-${index}`;
      if (!String(item.name || '').trim()) errors[`${key}-name`] = 'Informe o nome do material.';
      if (!bounded(item.packPrice, 0, 1e9)) errors[`${key}-packPrice`] = 'Informe o preço de compra.';
      if (!bounded(item.packQuantity, 0.0001, 1e9)) errors[`${key}-packQuantity`] = 'A quantidade da embalagem deve ser maior que zero.';
      if (!bounded(item.used, 0, 1e9)) errors[`${key}-used`] = 'Informe a quantidade utilizada.';
      const cost = money(item.packPrice * item.used / item.packQuantity);
      if (!Number.isSafeInteger(cost) || cost > 1e11) errors[`${key}-used`] = 'O custo calculado ultrapassa o limite da simulação.';
      return { ...item, cost };
    });
    if (materials.length > 100) errors.materials = 'Use até 100 materiais por simulação.';
    if (input.actualPrice !== null && !bounded(input.actualPrice, 0.01, 1e9)) {
      errors.actualPrice = 'Informe um preço maior que zero ou deixe o campo vazio.';
    }
    if (Object.keys(errors).length) return { ok: false, errors };

    const capacity = input.days * input.hours * input.rooms * (input.occupancy / 100);
    const hourlyCost = input.monthlyCost / capacity;
    const timeCost = money(hourlyCost * input.duration / 60);
    const materialCost = materials.reduce((sum, item) => sum + item.cost, 0);
    const base = materialCost + timeCost;
    if (base <= 0 || base > 1e11) errors.monthlyCost = 'Informe custos para calcular um preço sugerido válido.';
    const clinicShare = 1 - input.commission / 100;
    const margin = input.margin / 100;
    // The target is the CLINIC's margin after sharing the remaining profit.
    const divisor = 1 - input.tax / 100 - input.card / 100 - (margin ? margin / clinicShare : 0);
    if (!Number.isFinite(divisor) || divisor <= 1e-9) {
      errors.margin = 'Essa margem não é possível com os impostos, taxas e comissão informados. Reduza um dos percentuais.';
    }
    if (Object.keys(errors).length) return { ok: false, errors };
    function split(price) {
      const taxes = Math.round(price * input.tax / 100);
      const fees = Math.round(price * input.card / 100);
      const costs = base + taxes + fees;
      const profit = price - costs;
      const professional = profit > 0 ? Math.round(profit * input.commission / 100) : 0;
      const clinic = profit - professional;
      return { price, taxes, fees, costs, profit, professional, clinic, actualMargin: clinic / price * 100 };
    }
    const beforeProfit = 1 - input.tax / 100 - input.card / 100;
    let minimum = Math.ceil(base / beforeProfit - 1e-8);
    if (split(minimum).profit < 0) minimum = Math.ceil((base + 1) / beforeProfit);
    let suggested = Math.max(minimum, Math.ceil(base / divisor - 1e-8));
    // Cover rounding of two fees and the profit split without missing the target.
    if (clinicShare && split(suggested).actualMargin + 1e-9 < input.margin) {
      suggested = Math.ceil((base + 1 + 0.5 / clinicShare) / divisor);
    }
    if (!Number.isSafeInteger(suggested) || suggested > 1e11) {
      return { ok: false, errors: { margin: 'O preço sugerido ultrapassa o limite da simulação. Revise os percentuais.' } };
    }
    const actual = split(input.actualPrice === null ? suggested : money(input.actualPrice));
    return { ok: true, errors, materials, capacity, hourlyCost, materialCost, timeCost, base,
      suggested, minimum,
      commissionRate: input.commission, targetMargin: input.margin, ...actual };
  }
  const api = { parseNumber, calculate };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PricingMath = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
