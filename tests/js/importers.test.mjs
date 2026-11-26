// Reads real files (made by Excel-compatible tools, LibreOffice, Word-compatible tools) through the import readers.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";

const I = createRequire(import.meta.url)("../../public/importers.js");
const dir = new URL("./fixtures/", import.meta.url);
const read = (name) => { const b = fs.readFileSync(new URL(name, dir)); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); };

const HEAD = ["Product Name", "SKU", "Quantity", "Minimum", "Unit"];
const ROWS = [
  ["Notebook A5", "NB-050", "40", "20", "pack"], ["Stapler, heavy duty", "ST-203", "12", "5", "piece"],
  ["Café lamp ü", "DL-330", "7", "8", "piece"], ["Pens & markers", "PM-001", "100", "30", "box"],
];
const WANT = [HEAD, ...ROWS];

const SAME = {
  "text with commas": "products.csv", "semicolons (European Excel)": "products-semicolon.csv", "tabs": "products.tsv",
  "pipes": "products-pipe.txt", "Windows 1252 text": "products-win1252.csv", "JSON list": "products.json",
  "JSON inside an object": "products-wrapped.json", "JSON Lines": "products.jsonl", "JSON list of lists": "products-rows.json",
  "Markdown table": "products.md", "Excel (openpyxl)": "products-openpyxl.xlsx", "Excel saved by LibreOffice": "products-libreoffice.xlsx",
  "OpenDocument by LibreOffice": "products-libreoffice.ods", "OpenDocument by odfpy": "products-odfpy.ods", "Word table": "products.docx",
  "PowerPoint table": "products.pptx", "PDF from Word": "products.pdf", "PDF from a report library": "products-reportlab.pdf",
  "PDF from a spreadsheet (right aligned numbers)": "products-calc.pdf",
};
for (const [label, file] of Object.entries(SAME)) {
  test("reads " + label, async () => {
    const { table } = await I.parseBuffer(file, read(file));
    assert.deepEqual(table, WANT);
  });
}

test("a sparse Excel sheet keeps cells where they are", async () => {
  const { table, kind } = await I.parseBuffer("products-xlsxwriter.xlsx", read("products-xlsxwriter.xlsx"));
  assert.equal(kind, "Excel");
  assert.deepEqual(table[0], [...HEAD, "", ""]);
  assert.deepEqual(table.at(-1), ["", "", "", "", "", "", "stray far cell"]);
});

test("a PDF is recognised by its content even with a wrong name", async () => {
  assert.equal((await I.parseBuffer("data.txt", read("products-reportlab.pdf"))).kind, "PDF");
  assert.equal((await I.parseBuffer("slides.zip", read("products.pptx"))).kind, "PowerPoint");
});

test("the file name does not matter: the content decides", async () => {
  assert.equal((await I.parseBuffer("export.dat", read("products-libreoffice.xlsx"))).kind, "Excel");
  assert.equal((await I.parseBuffer("noextension", read("products.docx"))).kind, "Word");
  assert.equal((await I.parseBuffer("stock", read("products.json"))).kind, "JSON");
  assert.equal((await I.parseBuffer("stock.bin", read("products-semicolon.csv"))).kind, "Text");
  assert.equal((await I.parseBuffer("report.zip", read("products-libreoffice.ods"))).kind, "OpenDocument");
});

test("formats that cannot be read say so and how to fix it", async () => {
  const ask = (name, file) => I.parseBuffer(name, read(file)).then(() => "read", (e) => e.message);
  assert.match(await ask("old.xls", "products-openpyxl.xls"), /Old Excel \(\.xls\).*\.xlsx/);
  assert.match(await ask("photo.png", "scan.png"), /Pictures and scans/);
  assert.match(await ask("renamed.csv", "scan.png"), /Pictures and scans/, "a picture renamed to .csv is still a picture");
  assert.match(await ask("old.doc", "products-openpyxl.xls"), /Old Word \(\.doc\).*\.docx/);
  assert.match(await ask("old.ppt", "products-openpyxl.xls"), /Old PowerPoint \(\.ppt\).*\.pptx/);
  assert.match(await ask("scan.pdf", "no-text.pdf"), /no text that can be read.*scan/);
  assert.match(await ask("report.pdf", "no-table.pdf"), /No table with column names was found in that PDF/);
  assert.equal(await ask("export.xls", "products.csv"), "read", "an .xls that is really a text table is read by its content");
  assert.equal(await ask("export.pdf", "products.csv"), "read", "the same goes for a text file with a misleading name");
});

test("broken files fail with a readable message, not a crash", async () => {
  const text = (s) => new TextEncoder().encode(s).buffer;
  const cut = read("products-libreoffice.xlsx").slice(0, 300); // a download that stopped half way
  await assert.rejects(I.parseBuffer("a.xlsx", cut), /not a valid zip/);
  assert.equal((await I.parseBuffer("a.xlsx", text("name,qty\nPens,5"))).kind, "Text", "a CSV saved with an Excel name is still read");
  await assert.rejects(I.parseBuffer("a.json", text("{broken")), SyntaxError);
  await assert.rejects(I.parseBuffer("a.json", text("[1, 2, 3]")), /list of objects/);
  const plainZip = read("products-libreoffice.xlsx");
  assert.ok((await I.parseBuffer("a.zip", plainZip)).table.length > 1, "a zip that holds a workbook is opened");
});

test("CSV details: quotes, blank lines, newlines inside quotes", () => {
  assert.deepEqual(I.parseDelimited('a,b\n"x, y","say ""hi"""\n\n,\n'), [["a", "b"], ["x, y", 'say "hi"']]);
  assert.deepEqual(I.parseDelimited('a,b\n"two\nlines",z'), [["a", "b"], ["two\nlines", "z"]]);
});

test("Markdown without a table is read as plain text", async () => {
  const { kind, table } = await I.parseBuffer("notes.md", new TextEncoder().encode("name,qty\nPens,5\n").buffer);
  assert.equal(kind, "Text");
  assert.deepEqual(table, [["name", "qty"], ["Pens", "5"]]);
});
