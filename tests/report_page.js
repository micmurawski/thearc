// Runs a generated report's script against a minimal fake DOM and prints what each view rendered, as JSON.
// Used by tests/test_report_page.py: a script error fails the test instead of a viewer's page.
//
//   node tests/report_page.js report.html
const fs = require("fs");

const html = fs.readFileSync(process.argv[2], "utf8");
const data = html.match(/<script type="application\/json" id="thearc-data">([\s\S]*?)<\/script>/)[1];
const code = html.match(/<script>\n(\(function \(\) \{[\s\S]*?\}\)\(\);)\n<\/script>/)[1];

const elements = {};
const handlers = {};
const element = (id) => elements[id] || (elements[id] = {
  id, innerHTML: "", textContent: id === "thearc-data" ? data : "", hidden: false, value: "", attrs: {},
  setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k] ?? null; }, onclick: null,
  addEventListener(type, fn) { handlers[`${id}:${type}`] = fn; },
});
const root = { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k] ?? null; } };
const store = {};
global.window = { matchMedia: () => ({ matches: false }) };
global.document = { documentElement: root, getElementById: element, querySelector: () => ({ addEventListener() {} }) };
global.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };

eval(code);  // eslint-disable-line no-eval

const count = (id, pattern) => (elements[id]?.innerHTML.match(pattern) || []).length;
const result = {
  graphRows: count("graph", /class="grow/g),
  mergedFromEdges: count("graph", /stroke-dasharray:4 4/g),
  gridCells: count("grid", /<td class="cell"/g),
  sectionVersions: count("section-view", /class="version"/g),
  changeCommits: count("changes", /class="change-commit"/g),
  detail: elements["graph-detail"]?.innerHTML.length || 0,
  documentLines: count("doc", /<span class="ln">/g),
};
const row = elements.graph.innerHTML.match(/data-commit="([0-9a-f]+)"/);
if (row) {  // Select a row: the graph re-renders with it selected.
  handlers["graph:click"]({ target: { closest: (sel) => sel === "button[data-commit]" ? { dataset: { commit: row[1] } } : null } });
  result.selected = elements.graph.innerHTML.includes(`class="grow on`) ? row[1] : null;
}
handlers["theme:click"]();
result.theme = root.attrs["data-theme"] || null;
handlers["g-bookkeeping:change"]({ target: { checked: true } });
result.graphRowsWithBookkeeping = count("graph", /class="grow/g);
process.stdout.write(JSON.stringify(result));
