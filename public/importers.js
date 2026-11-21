"use strict";
/* File readers for the product import. Every reader turns a file into a table: an array of rows, the first
   row being the column names. Nothing here touches the page or the network, and nothing is uploaded:
   the file is read inside the browser. */
(function (root) {
  const MAX_INFLATED = 30 * 1024 * 1024; // refuse zip bombs: no single part may unpack past this

  /* ---------- plain text tables ---------- */

  function parseDelimited(text) {
    const first = text.split(/\r?\n/, 1)[0] || "";
    const delim = [",", ";", "\t", "|"].map((d) => [d, first.split(d).length]).sort((a, b) => b[1] - a[1])[0][0];
    const rows = [];
    let row = [];
    let cur = "";
    let quoted = false;
    const endRow = () => {
      row.push(cur);
      cur = "";
      if (row.some((c) => c.trim() !== "")) rows.push(row);
      row = [];
    };
    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      if (quoted) {
        if (ch === "\"") {
          if (text[i + 1] === "\"") { cur += "\""; i += 1; } else quoted = false;
        } else cur += ch;
      } else if (ch === "\"") quoted = true;
      else if (ch === delim) { row.push(cur); cur = ""; }
      else if (ch === "\n" || ch === "\r") {
        if (ch === "\r" && text[i + 1] === "\n") i += 1;
        endRow();
      } else cur += ch;
    }
    endRow();
    return rows.map((r) => r.map((c) => c.trim()));
  }

  const flat = (v) => (v === null || v === undefined || typeof v === "object" ? "" : String(v));

  function tableFromObjects(list) {
    const headers = [];
    list.forEach((o) => Object.keys(o).forEach((k) => { if (!headers.includes(k)) headers.push(k); }));
    return [headers, ...list.map((o) => headers.map((h) => flat(o[h])))];
  }

  const isRecord = (o) => o !== null && typeof o === "object" && !Array.isArray(o);

  /* A JSON list of objects, a list of lists, an object holding such a list, or JSON Lines. */
  function tableFromJsonText(text) {
    let data;
    try {
      data = JSON.parse(text);
    } catch (err) {
      const lines = text.split(/\r?\n/).filter((l) => l.trim());
      if (lines.length < 2) throw err;
      data = lines.map((l) => JSON.parse(l));
    }
    if (isRecord(data)) {
      const inner = Object.values(data).find((v) => Array.isArray(v) && v.length);
      data = inner || [data];
    }
    if (!Array.isArray(data) || !data.length) throw new Error("A JSON file must hold a list of products.");
    if (data.every(Array.isArray)) return data.map((r) => r.map(flat));
    if (data.every(isRecord)) return tableFromObjects(data);
    throw new Error("A JSON file must be a list of objects, one per product.");
  }

  /* Markdown pipe tables: | name | sku | */
  function tableFromMarkdown(text) {
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l.startsWith("|") && l.length > 1);
    const rows = lines.map((l) => l.replace(/^\||\|$/g, "").split("|").map((c) => c.trim()))
      .filter((cells) => !cells.every((c) => /^:?-{2,}:?$/.test(c)));
    return rows.length ? rows : null;
  }

  /* ---------- XML, HTML (these use the browser's own parser) ---------- */

  /* Finds the element that repeats most (the records) and turns each one into a row. */
  function tableFromXml(text) {
    const doc = new DOMParser().parseFromString(text, "application/xml");
    if (doc.getElementsByTagName("parsererror").length) throw new Error("That XML file is not valid.");
    let best = null;
    const visit = (el) => {
      const groups = new Map();
      Array.from(el.children).forEach((c) => {
        if (!groups.has(c.tagName)) groups.set(c.tagName, []);
        groups.get(c.tagName).push(c);
        visit(c);
      });
      groups.forEach((list) => {
        const record = list.some((c) => c.children.length || c.attributes.length);
        if (record && (!best || list.length > best.length)) best = list;
      });
    };
    visit(doc.documentElement);
    if (!best) throw new Error("No list of products was found in that XML file.");
    const objects = best.map((rec) => {
      const o = {};
      Array.from(rec.attributes).forEach((a) => { o[a.name] = a.value; });
      Array.from(rec.children).forEach((c) => { if (!c.children.length) o[c.tagName] = c.textContent.trim(); });
      return o;
    });
    return tableFromObjects(objects);
  }

  function tableFromHtml(text) {
    const doc = new DOMParser().parseFromString(text, "text/html");
    const table = doc.querySelector("table");
    if (!table) throw new Error("No table was found in that HTML file.");
    const rows = Array.from(table.querySelectorAll("tr")).map((tr) => Array.from(tr.children)
      .filter((c) => /^(TD|TH)$/.test(c.tagName)).map((c) => c.textContent.replace(/\s+/g, " ").trim())).filter((r) => r.length);
    return rows;
  }

  /* ---------- zip based formats: Excel, OpenDocument, Word ---------- */

  async function inflate(bytes) {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    const out = new Uint8Array(await new Response(stream).arrayBuffer());
    if (out.length > MAX_INFLATED) throw new Error("That file unpacks to something far too large.");
    return out;
  }

  /* Minimal zip reader: finds the central directory and unpacks single entries on demand. */
  function openZip(buffer) {
    const bytes = new Uint8Array(buffer);
    const view = new DataView(buffer);
    let end = -1;
    for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i -= 1) {
      if (view.getUint32(i, true) === 0x06054b50) { end = i; break; }
    }
    if (end < 0) throw new Error("That file is not a valid zip based document.");
    const count = view.getUint16(end + 10, true);
    let p = view.getUint32(end + 16, true);
    const entries = new Map();
    for (let n = 0; n < count; n += 1) {
      if (view.getUint32(p, true) !== 0x02014b50) throw new Error("That file is damaged.");
      const method = view.getUint16(p + 10, true);
      const csize = view.getUint32(p + 20, true);
      const usize = view.getUint32(p + 24, true);
      const nameLen = view.getUint16(p + 28, true);
      const extraLen = view.getUint16(p + 30, true);
      const commentLen = view.getUint16(p + 32, true);
      const offset = view.getUint32(p + 42, true);
      const name = new TextDecoder().decode(bytes.subarray(p + 46, p + 46 + nameLen));
      entries.set(name, { method, csize, usize, offset });
      p += 46 + nameLen + extraLen + commentLen;
    }
    return {
      has: (name) => entries.has(name),
      names: () => Array.from(entries.keys()),
      async text(name) {
        const e = entries.get(name);
        if (!e) throw new Error("A part of that file is missing: " + name);
        if (e.usize > MAX_INFLATED) throw new Error("That file unpacks to something far too large.");
        const start = e.offset + 30 + view.getUint16(e.offset + 26, true) + view.getUint16(e.offset + 28, true);
        const raw = bytes.subarray(start, start + e.csize);
        const data = e.method === 0 ? raw : await inflate(raw);
        return new TextDecoder().decode(data);
      },
    };
  }

  function decodeXml(s) {
    return s.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (m, g) => {
      if (g[0] === "#") return String.fromCodePoint(g[1].toLowerCase() === "x" ? parseInt(g.slice(2), 16) : parseInt(g.slice(1), 10));
      return { amp: "&", lt: "<", gt: ">", quot: "\"", apos: "'" }[g.toLowerCase()];
    });
  }

  const attr = (tag, name) => {
    const m = tag.match(new RegExp("(?:^|[\\s])" + name.replace(":", "\\:") + "=\"([^\"]*)\""));
    return m ? decodeXml(m[1]) : null;
  };

  const trimTable = (rows) => {
    const kept = rows.filter((r) => r.some((c) => String(c).trim() !== ""));
    let width = 0;
    kept.forEach((r) => r.forEach((c, i) => { if (String(c).trim() !== "") width = Math.max(width, i + 1); }));
    return kept.map((r) => Array.from({ length: width }, (_, i) => (r[i] === undefined ? "" : String(r[i]).trim())));
  };

  const colIndex = (ref) => {
    const letters = (ref.match(/^[A-Za-z]+/) || ["A"])[0].toUpperCase();
    let n = 0;
    for (const ch of letters) n = n * 26 + (ch.charCodeAt(0) - 64);
    return n - 1;
  };

  async function tableFromXlsx(zip) {
    const textOf = (xml) => decodeXml(Array.from(xml.replace(/<rPh\b[\s\S]*?<\/rPh>/g, "").matchAll(/<t\b[^>]*>([\s\S]*?)<\/t>/g)).map((m) => m[1]).join(""));
    const shared = [];
    if (zip.has("xl/sharedStrings.xml")) {
      const xml = await zip.text("xl/sharedStrings.xml");
      Array.from(xml.matchAll(/<si\b[^>]*>([\s\S]*?)<\/si>/g)).forEach((m) => shared.push(textOf(m[1])));
    }
    let sheetPath = null;
    try {
      const book = await zip.text("xl/workbook.xml");
      const rid = attr((book.match(/<sheet\b[^>]*>/) || [""])[0], "r:id");
      const rels = await zip.text("xl/_rels/workbook.xml.rels");
      const rel = Array.from(rels.matchAll(/<Relationship\b[^>]*>/g)).map((m) => m[0]).find((t) => attr(t, "Id") === rid);
      const target = rel && attr(rel, "Target");
      if (target) sheetPath = target.startsWith("/") ? target.slice(1) : "xl/" + target.replace(/^\.\//, "");
    } catch (_) { /* fall back to the first worksheet below */ }
    if (!sheetPath || !zip.has(sheetPath)) sheetPath = zip.names().filter((n) => /^xl\/worksheets\/sheet\d+\.xml$/.test(n)).sort()[0];
    if (!sheetPath) throw new Error("No worksheet was found in that Excel file.");
    const xml = await zip.text(sheetPath);
    const rows = [];
    Array.from(xml.matchAll(/<row\b[^>]*?(?:\/>|>([\s\S]*?)<\/row>)/g)).forEach((rm) => {
      const cells = [];
      Array.from((rm[1] || "").matchAll(/<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g)).forEach((cm) => {
        const ref = attr(cm[1], "r");
        const type = attr(cm[1], "t");
        const body = cm[2] || "";
        const v = (body.match(/<v>([\s\S]*?)<\/v>/) || [])[1];
        let value = "";
        if (type === "s") value = shared[Number(v)] || "";
        else if (type === "inlineStr") value = textOf(body);
        else if (type === "str") value = decodeXml(v || "");
        else if (type === "b") value = v === "1" ? "TRUE" : "FALSE";
        else if (type === "e") value = "";
        else value = v === undefined ? "" : decodeXml(v);
        cells[ref ? colIndex(ref) : cells.length] = value;
      });
      rows.push(Array.from(cells, (c) => (c === undefined ? "" : c)));
    });
    return trimTable(rows);
  }

  async function tableFromOds(zip) {
    const xml = await zip.text("content.xml");
    const sheet = (xml.match(/<table:table\b[^>]*>([\s\S]*?)<\/table:table>/) || [])[1];
    if (!sheet) throw new Error("No sheet was found in that OpenDocument file.");
    const rows = [];
    Array.from(sheet.matchAll(/<table:table-row\b([^>]*?)(?:\/>|>([\s\S]*?)<\/table:table-row>)/g)).forEach((rm) => {
      const cells = [];
      Array.from((rm[2] || "").matchAll(/<table:(?:covered-)?table-cell\b([^>]*?)(?:\/>|>([\s\S]*?)<\/table:(?:covered-)?table-cell>)/g)).forEach((cm) => {
        const paragraphs = Array.from((cm[2] || "").matchAll(/<text:p\b[^>]*>([\s\S]*?)<\/text:p>/g)).map((m) => decodeXml(m[1].replace(/<[^>]+>/g, "")));
        const value = attr(cm[1], "office:value") !== null ? attr(cm[1], "office:value") : paragraphs.join(" ");
        const repeat = Math.min(Number(attr(cm[1], "table:number-columns-repeated")) || 1, value === "" ? 1 : 50);
        for (let i = 0; i < repeat; i += 1) cells.push(value);
      });
      const repeat = Math.min(Number(attr(rm[1], "table:number-rows-repeated")) || 1, cells.some((c) => c !== "") ? 1000 : 1);
      for (let i = 0; i < repeat; i += 1) rows.push(cells.slice());
    });
    return trimTable(rows);
  }

  async function tableFromDocx(zip) {
    const xml = await zip.text("word/document.xml");
    const table = (xml.match(/<w:tbl\b[^>]*>([\s\S]*?)<\/w:tbl>/) || [])[1];
    if (!table) throw new Error("No table was found in that Word file.");
    const rows = Array.from(table.matchAll(/<w:tr\b[^>]*>([\s\S]*?)<\/w:tr>/g)).map((tr) => Array.from(tr[1].matchAll(/<w:tc\b[^>]*>([\s\S]*?)<\/w:tc>/g)).map((tc) =>
      Array.from(tc[1].matchAll(/<w:p\b[^>]*>([\s\S]*?)<\/w:p>/g)).map((p) => decodeXml(Array.from(p[1].matchAll(/<w:t\b[^>]*>([\s\S]*?)<\/w:t>/g)).map((m) => m[1]).join(""))).join(" ")));
    return trimTable(rows);
  }

  /* ---------- deciding what a file is, then reading it ---------- */

  const BY_EXTENSION = {
    csv: "text", tsv: "text", tab: "text", txt: "text", dat: "text", psv: "text",
    json: "json", jsonl: "json", ndjson: "json", geojson: "json",
    xml: "xml", html: "html", htm: "html", md: "markdown", markdown: "markdown",
    xlsx: "zip", xlsm: "zip", xltx: "zip", ods: "zip", docx: "zip", zip: "zip",
  };

  const UNSUPPORTED = {
    xls: "Old Excel (.xls) files cannot be read in the browser. In Excel choose Save As and pick Excel Workbook (.xlsx) or CSV.",
    doc: "Old Word (.doc) files cannot be read. Save the document as .docx first.",
    pdf: "PDF files cannot be read reliably. Export the table from the PDF as CSV or Excel instead.",
    image: "Pictures and scans cannot be read as data. Type the table into a spreadsheet, or use a CSV or Excel file.",
    ppt: "Slide files cannot be imported. Copy the table into a spreadsheet and save it as CSV or Excel.",
  };
  const IMAGES = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "heic", "svg"];

  /* Binary file signatures decide before the file name does: a picture renamed to .csv is still a picture. */
  function signature(bytes) {
    const head = Array.from(bytes.subarray(0, 8));
    const is = (...sig) => sig.every((b, i) => head[i] === b);
    if (is(0x50, 0x4b)) return "zip";
    if (is(0x25, 0x50, 0x44, 0x46)) return "pdf";
    if (is(0xd0, 0xcf, 0x11, 0xe0)) return "ole"; // old Word, Excel and PowerPoint files
    if (is(0x89, 0x50, 0x4e, 0x47) || is(0xff, 0xd8, 0xff) || is(0x47, 0x49, 0x46) || is(0x42, 0x4d)) return "image";
    return null;
  }

  function sniff(bytes) {
    const text = new TextDecoder().decode(bytes.subarray(0, 2000)).replace(/^\uFEFF/, "").trimStart();
    if (text.startsWith("{") || text.startsWith("[")) return "json";
    if (/^<(\?xml|!doctype html|html|table)/i.test(text)) return /<(html|table)\b/i.test(text) && !/^<\?xml/i.test(text) ? "html" : "xml";
    if (text.startsWith("<")) return "xml";
    if (/^\|.*\|/m.test(text)) return "markdown";
    return "text";
  }

  function decodeText(bytes) {
    try {
      return new TextDecoder("utf-8", { fatal: true }).decode(bytes).replace(/^\uFEFF/, "");
    } catch (_) {
      return new TextDecoder("windows-1252").decode(bytes); // old Excel CSV exports
    }
  }

  async function parseBuffer(name, buffer) {
    const bytes = new Uint8Array(buffer);
    const ext = (name.split(".").pop() || "").toLowerCase();
    const sig = signature(bytes);
    if (sig === "pdf") throw new Error(UNSUPPORTED.pdf);
    if (sig === "image" || IMAGES.includes(ext)) throw new Error(UNSUPPORTED.image);
    if (sig === "ole") throw new Error(UNSUPPORTED[ext === "doc" || ext === "ppt" ? ext : "xls"]);
    // The content decides. Many ".xls" exports are really HTML or text tables, so the extension alone never rejects a file.
    let kind = sig || (BY_EXTENSION[ext] === "zip" ? "text" : BY_EXTENSION[ext]) || sniff(bytes);
    if (kind === "zip") {
      const zip = openZip(buffer);
      if (zip.has("xl/workbook.xml")) return { kind: "Excel", table: await tableFromXlsx(zip) };
      if (zip.has("content.xml")) return { kind: "OpenDocument", table: await tableFromOds(zip) };
      if (zip.has("word/document.xml")) return { kind: "Word", table: await tableFromDocx(zip) };
      throw new Error("That zip file does not contain an Excel, OpenDocument or Word document.");
    }
    const text = decodeText(bytes);
    if (kind === "json") return { kind: "JSON", table: tableFromJsonText(text) };
    if (kind === "xml") return { kind: "XML", table: tableFromXml(text) };
    if (kind === "html") return { kind: "HTML", table: tableFromHtml(text) };
    if (kind === "markdown") {
      const table = tableFromMarkdown(text);
      if (table) return { kind: "Markdown", table };
      kind = "text";
    }
    return { kind: "Text", table: parseDelimited(text) };
  }

  const api = { parseBuffer, parseDelimited, tableFromJsonText, tableFromMarkdown, tableFromXml, tableFromHtml, openZip, tableFromXlsx, tableFromOds, tableFromDocx, sniff };
  root.StockFlowImport = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
