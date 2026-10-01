const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");

const scope = vm.createContext({});
const source = fs.readFileSync(path.join(__dirname, "../static/financial-validation.js"), "utf8");
vm.runInContext(source.replace("const financialValidation = new FinancialValidation();", "this.FinancialValidation = FinancialValidation;"), scope);
const context = {clinic: "Vielle Clinic", from: "2026-09-01", to: "2026-09-30", doctor: "Teste"};

function controller() {
  const instance = Object.create(scope.FinancialValidation.prototype);
  instance.datasets = new Map([["competence", {pending: [{id: "example"}], context}]]);
  instance.source = "competence";
  instance.closed = 0;
  instance.rendered = 0;
  instance.dialog = {close: () => { instance.closed++; }};
  instance.render = () => { instance.rendered++; };
  return instance;
}

test("same-context refresh keeps the validation dialog open and updates its rows", () => {
  const instance = controller();
  instance.prepare("competence", {...context});
  instance.set("competence", [{id: "updated"}], {...context});
  assert.equal(instance.closed, 0);
  assert.equal(instance.rendered, 1);
  assert.equal(instance.datasets.get("competence").pending[0].id, "updated");
});

test("changing clinic, period or professional invalidates the old pending records", () => {
  for (const key of ["clinic", "from", "to", "doctor"]) {
    const instance = controller();
    instance.prepare("competence", {...context, [key]: "changed"});
    assert.equal(instance.closed, 1);
    assert.equal(instance.datasets.has("competence"), false);
  }
});

test("clearing another source does not close the active validation dialog", () => {
  const instance = controller();
  instance.clear("receipts");
  assert.equal(instance.closed, 0);
  assert.equal(instance.datasets.has("competence"), true);
  instance.clear("competence");
  assert.equal(instance.closed, 1);
});
