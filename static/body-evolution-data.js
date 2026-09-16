"use strict";
(() => {
  // exam_at is the local date/time printed on the exam, not the upload timestamp.
  const examDay = record => record.exam_at.slice(0, 10);
  const ordered = records => [...records].sort((a, b) =>
    a.exam_at.localeCompare(b.exam_at) || (a.created_at || "").localeCompare(b.created_at || "") || a.id.localeCompare(b.id));
  function groupDays(records) {
    const days = new Map();
    for (const record of ordered(records)) {
      const day = examDay(record);
      if (!days.has(day)) days.set(day, {day, records: []});
      days.get(day).records.push(record);
    }
    return [...days.values()];
  }
  function primaryRecord(records) {
    // Composition comes from bioimpedance when present. Never average or overwrite sources.
    const latest = ordered(records).reverse();
    return ["inbody", "manual", "handymet"].map(source => latest.find(row => row.source === source)).find(Boolean) || latest[0];
  }
  const throughDay = (records, day) => ordered(records).filter(record => examDay(record) <= day);
  const api = {examDay, groupDays, primaryRecord, throughDay};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.Doc4DocsBodyDates = api;
})();
