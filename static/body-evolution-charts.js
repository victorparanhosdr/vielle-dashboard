"use strict";
(() => {
  const definitions = {
    composition: [
      {key:"weight_kg", label:"Peso", unit:"kg", axis:"mass", type:"bar", color:"#174d40", light:"#46856a"},
      {key:"muscle_kg", label:"Massa muscular", unit:"kg", axis:"mass", type:"bar", color:"#618a75", light:"#b0c8b7"},
      {key:"fat_pct", label:"Gordura corporal", unit:"%", axis:"fat", type:"line", color:"#858c65", light:"#a7ad8b"},
    ],
    measurements: [
      {key:"waist_cm", label:"Abdômen", unit:"cm", axis:"length", type:"line", color:"#27614a", light:"#89b09b"},
      {key:"hip_cm", label:"Quadril", unit:"cm", axis:"length", type:"line", color:"#858c65", light:"#a7ad8b"},
    ],
  };
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const numeric = value => typeof value === "number" && Number.isFinite(value) ? value : null;
  const format = value => value == null ? "Não informado" : value.toLocaleString("pt-BR", {maximumFractionDigits:2});
  const stamp = value => new Date(value).toLocaleString("pt-BR", {dateStyle:"short",timeStyle:"short"});

  function scaleFor(values, zero = false) {
    const valid = values.filter(value => numeric(value) != null);
    if (!valid.length) return null;
    const low = Math.min(...valid), high = Math.max(...valid);
    const pad = zero ? Math.max(high * .08, 1) : Math.max((high - low) * .15, 2);
    const bottom = zero ? 0 : Math.max(0, low - pad), top = high + pad;
    const rough = (top - bottom) / 4, power = 10 ** Math.floor(Math.log10(rough));
    const step = [1, 2, 2.5, 5, 10].find(value => value * power >= rough) * power;
    const min = zero ? 0 : Math.floor(bottom / step) * step, max = Math.ceil(top / step) * step;
    const ticks = Array.from({length:Math.round((max - min) / step) + 1}, (_, i) => Number((min + i * step).toFixed(8)));
    return {min, max, ticks};
  }

  function buildModel(records, kind, hidden = new Set()) {
    const rows = [...records].sort((a,b) => a.exam_at.localeCompare(b.exam_at) || String(a.id).localeCompare(String(b.id)));
    const series = definitions[kind].map(definition => ({...definition,
      visible:!hidden.has(definition.key), values:rows.map(row => numeric(row.fields?.[definition.key])),
    }));
    const axes = {};
    for (const axis of new Set(series.map(item => item.axis))) {
      axes[axis] = scaleFor(series.filter(item => item.visible && item.axis === axis).flatMap(item => item.values), axis !== "length");
    }
    return {rows, series, axes, empty:!Object.values(axes).some(Boolean)};
  }

  // An absent measurement interrupts its line; it is never zero or interpolated.
  function segments(values) {
    const result = [];
    values.forEach((value,index) => {
      if (numeric(value) == null) return;
      if (index === 0 || numeric(values[index - 1]) == null) result.push([]);
      result.at(-1).push(index);
    });
    return result;
  }

  function geometry(model, width, height) {
    const left = width < 400 ? 38 : 46, right = width - (model.axes.fat ? (width < 400 ? 36 : 44) : 14);
    const top = 26, bottom = height - 38, slot = (right - left) / Math.max(1,model.rows.length);
    return {left,right,top,bottom,slot,
      x:index => left + slot * (index + .5),
      y:(value,axis) => bottom - (value - axis.min) / (axis.max - axis.min) * (bottom - top),
    };
  }

  function chartSvg(model, width, height, id) {
    const g = geometry(model,width,height), {left,right,top,bottom,slot,x,y} = g;
    const leftAxis = model.axes.mass || model.axes.length, gridAxis = leftAxis || model.axes.fat;
    const leftUnit = model.axes.mass ? "kg" : "cm";
    const grid = gridAxis.ticks.map(value => `<line x1="${left}" x2="${right}" y1="${y(value,gridAxis)}" y2="${y(value,gridAxis)}" class="plotGrid"/>`).join("");
    const axisLabels = (axis,side,unit) => axis ? `<text x="${side === "left" ? left - 10 : right + 9}" y="13" text-anchor="${side === "left" ? "end" : "start"}" class="axisUnit">${unit}</text>${axis.ticks.map(value=>`<text x="${side === "left" ? left - 10 : right + 9}" y="${y(value,axis)+4}" text-anchor="${side === "left" ? "end" : "start"}">${format(value)}</text>`).join("")}` : "";
    const active = model.series.filter(series => series.visible && model.axes[series.axis]);
    const gradients = active.map(series => `<linearGradient id="${id}-${series.key}-bar" x1="0" x2="0" y1="1" y2="0"><stop stop-color="${series.color}"/><stop offset="1" stop-color="${series.light}"/></linearGradient><linearGradient id="${id}-${series.key}-area" x1="0" x2="0" y1="0" y2="1"><stop stop-color="${series.light}" stop-opacity=".34"/><stop offset="1" stop-color="${series.light}" stop-opacity="0"/></linearGradient>`).join("");
    const lines = active.filter(series => series.type === "line");
    const areas = lines.map(series => segments(series.values).filter(part => part.length > 1).map(part => {
      const coordinates = part.map(index=>`${x(index)},${y(series.values[index],model.axes[series.axis])}`);
      return `<path d="M${x(part[0])},${bottom} L${coordinates.join(" L")} L${x(part.at(-1))},${bottom} Z" fill="url(#${id}-${series.key}-area)"/>`;
    }).join("")).join("");
    const bars = active.filter(series=>series.type === "bar"), gap = Math.min(6,slot*.08);
    const barWidth = Math.max(.5,Math.min(34,(slot*.72-gap)/Math.max(1,bars.length)));
    const barsMarkup = bars.map((series,order)=>series.values.map((value,index)=>{
      if (value == null) return "";
      const bx = x(index) - (barWidth*bars.length+gap*(bars.length-1))/2 + order*(barWidth+gap);
      const by = y(value,model.axes[series.axis]), radius = Math.min(5,barWidth/3,(bottom-by)/2);
      const path = `M${bx},${bottom} V${by+radius} Q${bx},${by} ${bx+radius},${by} H${bx+barWidth-radius} Q${bx+barWidth},${by} ${bx+barWidth},${by+radius} V${bottom} Z`;
      const nearLine = lines.some(line => line.values[index] != null && Math.abs(y(line.values[index],model.axes[line.axis])-by) < 22);
      const label = slot >= 100 && !nearLine ? `<text class="plotValue" x="${bx+barWidth/2}" y="${by-9}" text-anchor="middle">${format(value)}</text>` : "";
      return `<path d="${path}" data-series="${series.key}" fill="url(#${id}-${series.key}-bar)"/>${label}`;
    }).join("")).join("");
    const lineMarkup = lines.map(series => segments(series.values).map(part => {
      const points = part.map(index=>`${x(index)},${y(series.values[index],model.axes[series.axis])}`).join(" ");
      return `<polyline points="${points}" fill="none" stroke="white" stroke-width="4.5" stroke-opacity=".75"/><polyline points="${points}" data-series="${series.key}" fill="none" stroke="${series.color}" stroke-width="2.3"/>${part.map(index=>{
        const py = y(series.values[index],model.axes[series.axis]);
        const nearOther = active.some(other=>other !== series && other.values[index] != null && Math.abs(y(other.values[index],model.axes[other.axis])-py) < 24);
        const label = slot >= 85 && !nearOther ? `<text class="plotValue" x="${x(index)}" y="${py-11}" text-anchor="middle" style="fill:${series.color}">${format(series.values[index])}</text>` : "";
        return `<circle cx="${x(index)}" cy="${py}" r="4" fill="${series.color}" stroke="white" stroke-width="1.8"/>${label}`;
      }).join("")}`;
    }).join("")).join("");
    const labelCount = Math.max(2,Math.floor((right-left)/90));
    const indices = new Set(Array.from({length:Math.min(model.rows.length,labelCount)},(_,i)=>Math.round(i*(model.rows.length-1)/Math.max(1,Math.min(model.rows.length,labelCount)-1))));
    const dates = [...indices].map(index=>{
      const exam = model.rows[index].exam_at;
      const label = new Date(exam).toLocaleDateString("pt-BR",{day:"2-digit",month:"short"}).replace(" de "," ").replace(".","");
      const anchor = index === 0 ? "start" : index === model.rows.length-1 ? "end" : "middle";
      return `<text x="${x(index)}" y="${bottom+19}" text-anchor="${model.rows.length === 1 ? "middle" : anchor}">${escape(label)}</text><text x="${x(index)}" y="${bottom+33}" text-anchor="${model.rows.length === 1 ? "middle" : anchor}" class="axisYear">${escape(exam.slice(0,4))}</text>`;
    }).join("");
    const hits = model.rows.map((row,index)=>`<rect class="chartHit" data-index="${index}" x="${left+index*slot}" y="${top}" width="${slot}" height="${bottom-top}" fill="transparent" tabindex="${index === model.rows.length-1 ? 0 : -1}" role="button" aria-label="${escape(stamp(row.exam_at)+': '+model.series.filter(s=>s.visible).map(s=>`${s.label}: ${format(s.values[index])}${s.values[index] == null ? '' : ' '+s.unit}`).join('; '))}"/>`).join("");
    return `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="group" aria-label="${id === 'compositionChart' ? 'Composição corporal' : 'Medidas corporais'} por data do exame"><defs>${gradients}</defs>${grid}${axisLabels(leftAxis,"left",leftUnit)}${axisLabels(model.axes.fat,"right","%")}${areas}${barsMarkup}${lineMarkup}${dates}<g class="chartHighlight" hidden><line y1="${top}" y2="${bottom}" stroke="#74876a" stroke-dasharray="3 4"/><g class="highlightPoints"></g></g>${hits}</svg>`;
  }

  function create(host, kind) {
    const hidden = new Set();
    let records = [], model, g, activeIndex = -1, pinned = false, observedWidth = 0;
    host.innerHTML = `<div class="seriesLegend" role="group" aria-label="Medidas visíveis">${definitions[kind].map(series=>`<label style="--series-color:${series.color}"><input type="checkbox" data-series="${series.key}" checked><span class="seriesSymbol ${series.type}" aria-hidden="true"></span><span>${series.label}<small> · ${series.unit}</small></span></label>`).join("")}</div><div class="evolutionPlot"><div class="plotDrawing"></div><div class="chartTooltip" role="tooltip" hidden></div></div><p class="srOnly chartAnnouncement" aria-live="polite"></p>`;
    const plot = host.querySelector(".evolutionPlot"), drawing = host.querySelector(".plotDrawing"), tooltip = host.querySelector(".chartTooltip"), announcement = host.querySelector(".chartAnnouncement");
    function clear() {
      activeIndex = -1; pinned = false; tooltip.hidden = true;
      drawing.querySelector(".chartHighlight")?.setAttribute("hidden","");
    }
    function show(index, announce = false) {
      const row = model.rows[index];
      if (!row || model.empty) return;
      const changed = activeIndex !== index;
      activeIndex = index;
      const visible = model.series.filter(series=>series.visible);
      tooltip.innerHTML = `<strong>${escape(stamp(row.exam_at))}</strong><dl>${visible.map(series=>`<div><dt><span style="background:${series.color}"></span>${series.label}</dt><dd>${format(series.values[index])}${series.values[index] == null ? "" : ` ${series.unit}`}</dd></div>`).join("")}</dl><p>${escape(row.method)}</p>`;
      tooltip.hidden = false;
      const width = plot.clientWidth, tipWidth = tooltip.offsetWidth, xx = g.x(index);
      const tipLeft = xx + 16 + tipWidth < width ? xx + 16 : xx - tipWidth - 16;
      tooltip.style.left = `${Math.max(0,Math.min(width-tipWidth,tipLeft))}px`;
      tooltip.style.top = "32px";
      const highlight = drawing.querySelector(".chartHighlight");
      highlight.removeAttribute("hidden");
      highlight.querySelector("line").setAttribute("x1",xx);
      highlight.querySelector("line").setAttribute("x2",xx);
      const bars = visible.filter(series=>series.type === "bar" && model.axes[series.axis]);
      const gap = Math.min(6,g.slot*.08), barWidth = Math.max(.5,Math.min(34,(g.slot*.72-gap)/Math.max(1,bars.length)));
      highlight.querySelector(".highlightPoints").innerHTML = visible.filter(series=>series.values[index] != null && model.axes[series.axis]).map(series=>{
        const pointX = series.type === "bar" ? xx - (barWidth*bars.length+gap*(bars.length-1))/2 + bars.indexOf(series)*(barWidth+gap) + barWidth/2 : xx;
        return `<circle cx="${pointX}" cy="${g.y(series.values[index],model.axes[series.axis])}" r="7" fill="${series.color}" fill-opacity=".14" stroke="${series.color}" stroke-width="1.3"/>`;
      }).join("");
      if (announce && changed) announcement.textContent = `${stamp(row.exam_at)} · ${visible.map(s=>`${s.label}: ${format(s.values[index])}${s.values[index] == null ? '' : ' '+s.unit}`).join(' · ')} · ${row.method}`;
    }
    function paint() {
      const width = plot.clientWidth;
      if (!width) return;
      observedWidth = width;
      clear();
      model = buildModel(records,kind,hidden);
      if (model.empty) {
        drawing.innerHTML = `<p class="chartEmpty">${hidden.size === definitions[kind].length ? "Selecione uma medida na legenda." : "Sem medidas desta origem até a data selecionada."}</p>`;
        return;
      }
      const height = plot.clientHeight;
      g = geometry(model,width,height);
      drawing.innerHTML = chartSvg(model,width,height,host.id);
    }
    host.querySelectorAll("input[data-series]").forEach(input=>input.addEventListener("change",()=>{
      if (input.checked) hidden.delete(input.dataset.series); else hidden.add(input.dataset.series);
      paint();
    }));
    plot.addEventListener("pointermove",event=>{
      if (event.pointerType !== "mouse" || pinned || model?.empty) return;
      const hit = event.target.closest("[data-index]");
      if (hit) show(Number(hit.dataset.index)); else clear();
    });
    plot.addEventListener("pointerleave",()=>{if (!pinned && !plot.contains(document.activeElement)) clear();});
    plot.addEventListener("click",event=>{
      const hit = event.target.closest("[data-index]");
      if (hit) { pinned = true; show(Number(hit.dataset.index),true); }
    });
    plot.addEventListener("focusin",event=>{
      const hit = event.target.closest("[data-index]");
      if (hit) show(Number(hit.dataset.index),true);
    });
    plot.addEventListener("focusout",event=>{if (!pinned && !plot.contains(event.relatedTarget)) clear();});
    plot.addEventListener("keydown",event=>{
      if (event.key === "Escape") { clear(); return; }
      const hit = event.target.closest("[data-index]");
      if (!hit) return;
      const index = Number(hit.dataset.index), last = model.rows.length-1;
      const next = {ArrowLeft:Math.max(0,index-1),ArrowRight:Math.min(last,index+1),Home:0,End:last}[event.key];
      if (next !== undefined) {
        event.preventDefault(); pinned = false;
        drawing.querySelectorAll("[data-index]").forEach(node=>node.setAttribute("tabindex",Number(node.dataset.index) === next ? "0" : "-1"));
        drawing.querySelector(`[data-index="${next}"]`).focus();
      } else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); pinned = true; show(index,true); }
    });
    const outside = event => { if (!host.contains(event.target)) clear(); };
    document.addEventListener("pointerdown",outside);
    const observer = new ResizeObserver(()=>{if (plot.clientWidth && plot.clientWidth !== observedWidth) paint();});
    observer.observe(plot);
    return {render(rows) { records = rows; announcement.textContent = ""; paint(); }, destroy() { observer.disconnect(); document.removeEventListener("pointerdown",outside); }};
  }
  const api = {definitions,scaleFor,buildModel,segments,geometry,chartSvg,create};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.Doc4DocsBodyCharts = api;
})();
