/* Public, browser-only simulator. Never calls clinic APIs or persists inputs. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const currency = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
  const decimal = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const percentage = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 });
  const cash = cents => currency.format(cents / 100);
  const fields = ['monthlyCost', 'days', 'hours', 'rooms', 'occupancy', 'duration', 'tax', 'card', 'commission', 'margin'];
  const exampleMaterials = [
    { name: 'Insumo principal', packPrice: 650, packQuantity: 1, used: 1 },
    { name: 'Descartáveis', packPrice: 60, packQuantity: 1, used: 1 },
    { name: 'Apoio e higienização', packPrice: 15, packQuantity: 1, used: 1 },
  ];
  const colors = ['#246669', '#a4bbb2', '#809979', '#a6adb2', '#919d75', '#153e2e'];
  let snapshot;
  let pdfLibrary;

  function numberFrom(input) {
    if (input.type === 'number') return input.value.trim() ? Number(input.value) : NaN;
    return PricingMath.parseNumber(input.value);
  }
  function materialsFromForm() {
    return Array.from($('materials').children, row => ({
      name: row.querySelector('[data-key=name]').value.trim(),
      packPrice: numberFrom(row.querySelector('[data-key=packPrice]')),
      packQuantity: numberFrom(row.querySelector('[data-key=packQuantity]')),
      used: numberFrom(row.querySelector('[data-key=used]')),
    }));
  }
  function renderMaterials(items) {
    const fragment = document.createDocumentFragment();
    items.forEach((item, index) => {
      const row = document.createElement('div');
      row.className = 'material-row';
      for (const [key, labelText] of [['name', 'Material'], ['packPrice', 'Preço embalagem (R$)'], ['packQuantity', 'Qtd. embalagem'], ['used', 'Qtd. utilizada']]) {
        const label = document.createElement('label');
        const title = document.createElement('span');
        title.textContent = labelText;
        const input = document.createElement('input');
        input.dataset.key = key;
        input.id = `material-${index}-${key}`;
        input.setAttribute('aria-label', `${labelText} ${index + 1}`);
        const error = document.createElement('small');
        error.id = input.id + 'Error';
        error.className = 'field-error';
        input.setAttribute('aria-describedby', error.id);
        if (key === 'name') {
          input.maxLength = 100;
          input.value = item.name || '';
        } else if (key === 'packPrice') {
          input.inputMode = 'decimal';
          input.dataset.currency = '';
          input.value = Number.isFinite(item[key]) ? decimal.format(item[key]) : '';
        } else {
          input.type = 'number';
          input.min = key === 'packQuantity' ? '0.0001' : '0';
          input.max = '1000000000';
          input.step = 'any';
          input.value = Number.isFinite(item[key]) ? item[key] : '';
        }
        label.append(title, input, error);
        row.append(label);
      }
      const cost = document.createElement('strong');
      cost.className = 'material-cost';
      cost.id = `materialCost-${index}`;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'remove-material';
      remove.dataset.remove = index;
      remove.title = 'Remover material';
      remove.setAttribute('aria-label', `Remover material ${index + 1}`);
      remove.innerHTML = '<i data-lucide="trash-2"></i>';
      row.append(cost, remove);
      fragment.append(row);
    });
    $('materials').replaceChildren(fragment);
    $('materialCount').textContent = `${items.length} / 100`;
    $('addMaterial').disabled = items.length >= 100;
    lucide.createIcons();
  }

  function update(personalized = false) {
    if (personalized) $('simulationLabel').textContent = 'Simulação personalizada';
    const input = Object.fromEntries(fields.map(id => [id, numberFrom($(id))]));
    input.actualPrice = $('actualPrice').value.trim() ? numberFrom($('actualPrice')) : null;
    input.materials = materialsFromForm();
    const result = PricingMath.calculate(input);
    snapshot = { input, result, procedure: $('procedure').value.trim() || 'Procedimento personalizado' };
    document.querySelectorAll('[aria-invalid]').forEach(el => el.removeAttribute('aria-invalid'));
    document.querySelectorAll('.field-error').forEach(el => { el.textContent = ''; });
    for (const [key, message] of Object.entries(result.errors)) {
      if ($(key)) $(key).setAttribute('aria-invalid', 'true');
      if ($(key + 'Error')) $(key + 'Error').textContent = message;
    }
    input.materials.forEach((item, index) => {
      const amount = item.packPrice * item.used / item.packQuantity;
      $(`materialCost-${index}`).textContent = Number.isFinite(amount) && amount >= 0 ? currency.format(amount) : '—';
    });
    const ids = ['hourlyCost', 'materialTotal', 'timeCost', 'suggestedPrice', 'priceValue', 'costsValue', 'profitValue', 'professionalValue', 'clinicValue', 'minimumPrice'];
    $('exportPdf').disabled = !result.ok;
    $('errorSummary').hidden = result.ok;
    $('resultNumbers').hidden = !result.ok;
    $('targetMargin').textContent = Number.isFinite(input.margin) ? percentage.format(input.margin) + '%' : '—';
    if (!result.ok) {
      ids.forEach(id => { $(id).textContent = '—'; });
      $('errorSummary').textContent = result.errors.margin || 'Complete ou corrija os campos destacados para calcular a simulação.';
      return;
    }
    const values = [currency.format(result.hourlyCost), cash(result.materialCost), cash(result.timeCost),
      cash(result.suggested), cash(result.price), '− ' + cash(result.costs), cash(result.profit),
      cash(result.professional), cash(result.clinic), cash(result.minimum)];
    ids.forEach((id, index) => { $(id).textContent = values[index]; });
    $('profitLabel').textContent = result.profit < 0 ? 'Prejuízo da simulação' : 'Lucro para dividir';
    $('professionalLabel').textContent = `Profissional · ${percentage.format(input.commission)}%`;
    $('clinicLabel').textContent = `Clínica · ${percentage.format(100 - input.commission)}%`;
    $('marginStatus').className = 'margin-status' + (result.profit < 0 ? ' loss' : result.actualMargin + 1e-8 < input.margin ? ' warning' : '');
    $('marginText').textContent = `Margem final da clínica: ${percentage.format(result.actualMargin)}%` +
      (result.profit < 0 ? ' · Sem comissão' : result.actualMargin + 1e-8 < input.margin ? ' · Abaixo da meta' : '');
    const breakdown = [
      ['Materiais', result.materialCost], ['Tempo clínico', result.timeCost], ['Impostos', result.taxes],
      ['Pagamento', result.fees], ['Profissional', result.professional], ['Clínica', result.clinic],
    ];
    $('breakdown').replaceChildren();
    $('priceBar').replaceChildren();
    const denominator = breakdown.reduce((sum, [, value]) => sum + Math.max(0, value), 0);
    for (const [index, [label, amount]] of breakdown.entries()) {
      const row = document.createElement('div');
      const dt = document.createElement('dt');
      const swatch = document.createElement('span');
      swatch.className = 'swatch';
      swatch.style.background = colors[index];
      dt.append(swatch, document.createTextNode(label));
      const dd = document.createElement('dd');
      dd.textContent = cash(amount);
      row.append(dt, dd);
      $('breakdown').append(row);
      const segment = document.createElement('span');
      segment.className = 'price-segment';
      segment.style.background = colors[index];
      segment.style.width = (denominator ? Math.max(0, amount) / denominator * 100 : 0) + '%';
      segment.title = `${label}: ${cash(amount)}`;
      $('priceBar').append(segment);
    }
    $('priceBar').setAttribute('aria-label', breakdown.map(([label, value]) => `${label}: ${cash(value)}`).join('; '));
  }

  document.addEventListener('click', event => {
    const help = event.target.closest('[data-help]');
    if (help) $(help.dataset.help).showModal();
    const close = event.target.closest('[data-close]');
    if (close) close.closest('dialog').close('');
  });
  document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => {
    const bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
  }));
  $('pricingForm').addEventListener('submit', event => event.preventDefault());
  $('pricingForm').addEventListener('input', event => {
    if (event.target.id === 'occupancySlider') $('occupancy').value = event.target.value;
    if (event.target.id === 'occupancy') $('occupancySlider').value = event.target.value;
    update(true);
  });
  $('actualPrice').addEventListener('input', () => update(true));
  document.addEventListener('focusout', event => {
    if (event.target.matches('[data-currency]')) {
      const value = numberFrom(event.target);
      if (Number.isFinite(value)) {
        event.target.value = decimal.format(value);
        update();
      }
    }
  });
  $('addMaterial').addEventListener('click', () => {
    const rows = materialsFromForm();
    if (rows.length >= 100) return;
    rows.push({ name: '', packPrice: NaN, packQuantity: 1, used: 1 });
    renderMaterials(rows);
    update(true);
    $(`material-${rows.length - 1}-name`).focus();
  });
  $('materials').addEventListener('click', event => {
    const remove = event.target.closest('[data-remove]');
    if (!remove) return;
    const index = Number(remove.dataset.remove);
    const rows = materialsFromForm();
    rows.splice(index, 1);
    renderMaterials(rows);
    update(true);
    const next = $(`material-${Math.min(index, rows.length - 1)}-name`) || $('addMaterial');
    next.focus();
  });
  $('reset').addEventListener('click', () => {
    $('resetDialog').returnValue = '';
    $('resetDialog').showModal();
  });
  $('resetDialog').addEventListener('close', () => {
    if ($('resetDialog').returnValue !== 'confirm') return;
    $('pricingForm').reset();
    $('actualPrice').value = '2.000,00';
    renderMaterials(exampleMaterials);
    $('simulationLabel').textContent = 'Simulação de exemplo';
    update();
    $('actionStatus').textContent = 'Simulação reiniciada.';
  });

  function loadPdfLibrary() {
    if (!pdfLibrary) pdfLibrary = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/pricing-pdf-lib.min.js';
      script.onload = () => resolve(window.PDFLib);
      script.onerror = () => { script.remove(); pdfLibrary = null; reject(new Error('Biblioteca indisponível')); };
      document.head.append(script);
    });
    return pdfLibrary;
  }

  async function createPdf(data) {
    const { PDFDocument, StandardFonts, rgb } = await loadPdfLibrary();
    const pdf = await PDFDocument.create();
    pdf.setTitle('Precificação - DOC4DOCS');
    pdf.setAuthor('DOC4DOCS');
    const font = await pdf.embedFont(StandardFonts.Helvetica);
    const bold = await pdf.embedFont(StandardFonts.HelveticaBold);
    const ink = rgb(.07, .21, .17);
    const gray = rgb(.36, .44, .4);
    let page, y, pageCount = 0;
    const printable = value => String(value).normalize('NFC').replace(/[^\x20-\x7e\xa0-\xff]/g, '?');
    function nextPage() {
      page = pdf.addPage([595.28, 841.89]);
      pageCount++;
      page.drawRectangle({ x: 0, y: 775, width: 595.28, height: 67, color: ink });
      page.drawText('DOC4DOCS | Precificação', { x: 42, y: 801, size: 17, font: bold, color: rgb(1, 1, 1) });
      page.drawText(`Simulação financeira - ${new Date().toLocaleDateString('pt-BR')} | ${pageCount}`, { x: 42, y: 24, size: 8, font, color: gray });
      y = 748;
    }
    function reserve(height) { if (y - height < 48) nextPage(); }
    function lines(text, maxWidth = 510, size = 10, face = font) {
      const output = [];
      let line = '';
      for (let word of printable(text).split(/\s+/)) {
        if (line && face.widthOfTextAtSize(line + ' ' + word, size) > maxWidth) {
          output.push(line); line = '';
        }
        while (face.widthOfTextAtSize(word, size) > maxWidth) {
          let length = 1;
          while (length < word.length && face.widthOfTextAtSize(word.slice(0, length + 1), size) <= maxWidth) length++;
          output.push(word.slice(0, length));
          word = word.slice(length);
        }
        line += (line ? ' ' : '') + word;
      }
      if (line) output.push(line);
      return output;
    }
    function paragraph(text, size = 10, face = font) {
      for (const line of lines(text, 510, size, face)) {
        reserve(size + 6);
        page.drawText(line, { x: 42, y, size, font: face, color: ink });
        y -= size + 6;
      }
    }
    function heading(text) { reserve(44); y -= 12; paragraph(text, 12, bold); y -= 4; }
    function row(label, value) {
      const labelLines = lines(label, 350);
      reserve(Math.max(1, labelLines.length) * 16);
      page.drawText(printable(value), { x: 552 - font.widthOfTextAtSize(printable(value), 10), y, size: 10, font, color: ink });
      for (const line of labelLines) { page.drawText(line, { x: 42, y, size: 10, font, color: gray }); y -= 16; }
    }
    const { input, result: r } = data;
    nextPage();
    paragraph(data.procedure, 16, bold);
    heading('Resultado');
    row('Preço sugerido', cash(r.suggested));
    row('Preço praticado', cash(r.price));
    row('Preço de equilíbrio', cash(r.minimum));
    row('Custos, impostos e taxas', cash(r.costs));
    row(r.profit < 0 ? 'Prejuízo' : 'Lucro para dividir', cash(r.profit));
    row(`Profissional (${percentage.format(input.commission)}% do lucro positivo)`, cash(r.professional));
    row('Parte final da clínica', cash(r.clinic));
    row('Margem final da clínica / meta', `${percentage.format(r.actualMargin)}% / ${percentage.format(input.margin)}%`);
    heading('Estrutura da clínica');
    row('Custos mensais', currency.format(input.monthlyCost));
    row('Dias / horas por dia / salas / ocupação', `${input.days} / ${input.hours} / ${input.rooms} / ${input.occupancy}%`);
    row('Hora clínica', currency.format(r.hourlyCost));
    row(`Tempo do procedimento (${input.duration} min)`, cash(r.timeCost));
    heading('Ficha técnica');
    for (const item of r.materials) {
      row(item.name, cash(item.cost));
      paragraph(`Compra: ${currency.format(item.packPrice)} | Embalagem: ${item.packQuantity} | Utilizado: ${item.used}`, 8);
    }
    row('Total de materiais', cash(r.materialCost));
    heading('Impostos e remuneração');
    row(`Impostos (${input.tax}% do preço)`, cash(r.taxes));
    row(`Taxa de pagamento (${input.card}% do preço)`, cash(r.fees));
    y -= 10;
    paragraph('Comissão calculada sobre o lucro positivo após materiais, tempo clínico, impostos e taxas. Sem lucro, a comissão simulada é zero.', 8);
    paragraph('Valores estimados conforme os dados informados. Valide custos e alíquotas com sua contabilidade. Este documento não constitui apuração contábil.', 8);
    return pdf.save();
  }

  $('exportPdf').addEventListener('click', async () => {
    if (!snapshot.result.ok) return;
    const data = structuredClone(snapshot);
    const button = $('exportPdf');
    button.disabled = true;
    $('actionStatus').textContent = 'Gerando PDF...';
    try {
      const bytes = await createPdf(data);
      const url = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = 'doc4docs-precificacao.pdf';
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      $('actionStatus').textContent = 'PDF gerado com os valores da simulação.';
    } catch (_) {
      $('actionStatus').textContent = 'Não foi possível gerar o PDF. Verifique sua conexão e tente novamente.';
    } finally { button.disabled = !snapshot.result.ok; }
  });
  renderMaterials(exampleMaterials);
  update();
})();
