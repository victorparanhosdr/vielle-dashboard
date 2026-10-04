const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");

const scope = vm.createContext({URLSearchParams});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/financial-competence.js"), "utf8")
  + "\nthis.Report = FinancialCompetenceReport;", scope);

function controller() {
  const instance = Object.create(scope.Report.prototype);
  instance.context = {clinic: "inspire", clinicName: "Clínica Inspire"};
  instance.filters = {direction: "income", sort: "date", order: "desc", page: 2};
  instance.elements = Object.fromEntries(Object.entries({From: "2026-09-01", To: "2026-09-30", Doctor: "Teste",
    Search: "consulta", Contact: "patient-a", Category: "Consultas", Type: "Conta", DateBasis: "receipt", PaymentStatus: "partial"})
    .map(([key, value]) => [key, {value}]));
  return instance;
}

test("API and export query carry the selected date basis, payment status and professional", () => {
  const query = controller().query();
  assert.equal(query.get("date_basis"), "receipt");
  assert.equal(query.get("payment_status"), "partial");
  assert.equal(query.get("doctor"), "Teste");
  assert.equal(query.get("clinic"), "inspire");
  assert.equal(query.get("page"), "2");
  assert.equal(query.get("direction"), "income");
});

test("validation context changes with the payment status or date basis", () => {
  const report = controller();
  const before = report.validationContext();
  report.elements.DateBasis.value = "competence";
  assert.notEqual(JSON.stringify(report.validationContext()), JSON.stringify(before));
  report.elements.DateBasis.value = "receipt";
  report.elements.PaymentStatus.value = "all";
  assert.notEqual(JSON.stringify(report.validationContext()), JSON.stringify(before));
});
