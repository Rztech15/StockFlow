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

  /* PowerPoint (.pptx): the first table found on the slides. */
  async function tableFromPptx(zip) {
    const slides = zip.names().filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n))
      .sort((a, b) => Number(a.match(/(\d+)\.xml$/)[1]) - Number(b.match(/(\d+)\.xml$/)[1]));
    for (const name of slides) {
      const xml = await zip.text(name);
      const table = (xml.match(/<a:tbl\b[^>]*>([\s\S]*?)<\/a:tbl>/) || [])[1];
      if (!table) continue;
      const rows = Array.from(table.matchAll(/<a:tr\b[^>]*>([\s\S]*?)<\/a:tr>/g)).map((tr) => Array.from(tr[1].matchAll(/<a:tc\b[^>]*>([\s\S]*?)<\/a:tc>/g)).map((tc) =>
        Array.from(tc[1].matchAll(/<a:p\b[^>]*>([\s\S]*?)<\/a:p>/g)).map((p) => decodeXml(Array.from(p[1].matchAll(/<a:t\b[^>]*>([\s\S]*?)<\/a:t>/g)).map((m) => m[1]).join(""))).join(" ")));
      return trimTable(rows);
    }
    throw new Error("No table was found on the slides of that PowerPoint file.");
  }

  /* ---------- PDF: text that can be selected, laid out as a table ---------- */
  /* Reads text with its position, groups it into rows and columns, and takes the table. It works for PDFs made by
     Word, Excel, LibreOffice, reports and web exports. A scan or a picture of a table holds no text to read. */

  const latin1 = (bytes) => {
    let out = "";
    for (let i = 0; i < bytes.length; i += 8192) out += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
    return out;
  };
  const toBytes = (str) => Uint8Array.from(str, (c) => c.charCodeAt(0) & 255);

  async function inflateZlib(bytes) {
    try {
      const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
      const out = new Uint8Array(await new Response(stream).arrayBuffer());
      return out.length > MAX_INFLATED ? null : out;
    } catch (_) {
      return null;
    }
  }

  function ascii85(bytes) {
    const s = latin1(bytes).replace(/\s/g, "").replace(/^<~/, "");
    const end = s.indexOf("~>");
    const body = end >= 0 ? s.slice(0, end) : s;
    const out = [];
    let group = [];
    const flush = (count) => {
      let v = 0;
      for (let i = 0; i < 5; i += 1) v = v * 85 + (group[i] === undefined ? 84 : group[i]);
      [v >>> 24, (v >>> 16) & 255, (v >>> 8) & 255, v & 255].slice(0, count).forEach((b) => out.push(b));
    };
    for (const ch of body) {
      if (ch === "z" && group.length === 0) { out.push(0, 0, 0, 0); continue; }
      group.push(ch.charCodeAt(0) - 33);
      if (group.length === 5) { flush(4); group = []; }
    }
    if (group.length > 1) flush(group.length - 1);
    return Uint8Array.from(out);
  }

  async function applyFilters(data, dict) {
    const spec = (dict.match(/\/Filter\s*(\[[^\]]*\]|\/[A-Za-z0-9]+)/) || [])[1];
    if (!spec) return data;
    let out = data;
    for (const f of Array.from(spec.matchAll(/\/([A-Za-z0-9]+)/g)).map((m) => m[1])) {
      if (!out) return null;
      if (f === "FlateDecode" || f === "Fl") out = await inflateZlib(out);
      else if (f === "ASCII85Decode" || f === "A85") out = ascii85(out);
      else if (f === "ASCIIHexDecode" || f === "AHx") out = toBytes(latin1(out).replace(/[^0-9a-fA-F]/g, "").replace(/(..)/g, (h) => String.fromCharCode(parseInt(h, 16))));
      else return null; // image and other compressions: nothing to read there
    }
    return out;
  }

  async function readPdfObjects(bytes) {
    const text = latin1(bytes);
    const objs = new Map();
    const re = /(\d+)\s+(\d+)\s+obj\b/g;
    let m;
    while ((m = re.exec(text))) {
      const start = re.lastIndex;
      const end = text.indexOf("endobj", start);
      if (end < 0) break;
      const body = text.slice(start, end);
      const si = body.indexOf("stream");
      let dict = body;
      let data = null;
      if (si >= 0) {
        dict = body.slice(0, si);
        let from = start + si + 6;
        if (text[from] === "\r") from += 1;
        if (text[from] === "\n") from += 1;
        let to = start + body.lastIndexOf("endstream");
        if (text[to - 1] === "\n") to -= 1;
        if (text[to - 1] === "\r") to -= 1;
        data = bytes.subarray(from, Math.max(from, to));
        data = await applyFilters(data, dict);
      }
      objs.set(Number(m[1]), { dict, data });
    }
    // objects packed inside object streams (PDF 1.5 and later)
    for (const o of Array.from(objs.values())) {
      if (!o.data || !/\/Type\s*\/ObjStm/.test(o.dict)) continue;
      const count = Number((o.dict.match(/\/N\s+(\d+)/) || [])[1]);
      const first = Number((o.dict.match(/\/First\s+(\d+)/) || [])[1]);
      const inner = latin1(o.data);
      const head = inner.slice(0, first).trim().split(/\s+/).map(Number);
      for (let i = 0; i < count; i += 1) {
        const num = head[i * 2];
        const off = first + head[i * 2 + 1];
        const next = i + 1 < count ? first + head[i * 2 + 3] : inner.length;
        if (!objs.has(num)) objs.set(num, { dict: inner.slice(off, next), data: null });
      }
    }
    return objs;
  }

  function balanced(str, at) {
    let depth = 0;
    for (let i = at; i < str.length - 1; i += 1) {
      if (str[i] === "<" && str[i + 1] === "<") { depth += 1; i += 1; }
      else if (str[i] === ">" && str[i + 1] === ">") { depth -= 1; i += 1; if (depth === 0) return str.slice(at, i + 1); }
    }
    return str.slice(at);
  }

  /* The text of a dictionary value: an inline dictionary, or the object a reference points to. */
  function pdfValue(objs, dict, key) {
    const m = new RegExp("/" + key + "(?![A-Za-z])\\s*").exec(dict);
    if (!m) return null;
    const at = m.index + m[0].length;
    if (dict.startsWith("<<", at)) return balanced(dict, at);
    const ref = dict.slice(at).match(/^(\d+)\s+\d+\s+R/);
    if (ref) { const o = objs.get(Number(ref[1])); return o ? o.dict : null; }
    return dict.slice(at, at + 200);
  }

  function pdfPages(objs) {
    const pages = [];
    const visit = (num, depth) => {
      const o = objs.get(num);
      if (!o || depth > 40) return;
      if (/\/Type\s*\/Pages(?![A-Za-z])/.test(o.dict)) {
        const kids = (o.dict.match(/\/Kids\s*\[([^\]]*)\]/) || [])[1] || "";
        Array.from(kids.matchAll(/(\d+)\s+\d+\s+R/g)).forEach((k) => visit(Number(k[1]), depth + 1));
      } else if (/\/Type\s*\/Page(?![A-Za-z])/.test(o.dict)) pages.push(num);
    };
    for (const o of objs.values()) {
      if (/\/Type\s*\/Catalog/.test(o.dict)) {
        const root = (o.dict.match(/\/Pages\s+(\d+)\s+\d+\s+R/) || [])[1];
        if (root) visit(Number(root), 0);
        break;
      }
    }
    if (!pages.length) Array.from(objs.entries()).forEach(([n, o]) => { if (/\/Type\s*\/Page(?![A-Za-z])/.test(o.dict)) pages.push(n); });
    return pages;
  }

  const hexText = (hex) => {
    if (hex.length <= 2) return String.fromCharCode(parseInt(hex, 16));
    let out = "";
    for (let i = 0; i + 3 < hex.length + 1; i += 4) out += String.fromCharCode(parseInt(hex.slice(i, i + 4).padEnd(4, "0"), 16));
    return out;
  };

  function parseCMap(str) {
    const map = new Map();
    let codeBytes = 1;
    Array.from(str.matchAll(/beginbfchar([\s\S]*?)endbfchar/g)).forEach((m) => {
      Array.from(m[1].matchAll(/<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>/g)).forEach((p) => {
        codeBytes = Math.max(codeBytes, Math.ceil(p[1].length / 2));
        map.set(parseInt(p[1], 16), hexText(p[2]));
      });
    });
    Array.from(str.matchAll(/beginbfrange([\s\S]*?)endbfrange/g)).forEach((m) => {
      Array.from(m[1].matchAll(/<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*(<[0-9a-fA-F]+>|\[[^\]]*\])/g)).forEach((r) => {
        const lo = parseInt(r[1], 16);
        const hi = Math.min(parseInt(r[2], 16), lo + 65535);
        codeBytes = Math.max(codeBytes, Math.ceil(r[1].length / 2));
        if (r[3][0] === "[") {
          Array.from(r[3].matchAll(/<([0-9a-fA-F]+)>/g)).forEach((x, i) => { if (lo + i <= hi) map.set(lo + i, hexText(x[1])); });
        } else {
          const base = hexText(r[3].slice(1, -1));
          for (let c = lo; c <= hi; c += 1) map.set(c, base.slice(0, -1) + String.fromCharCode(base.charCodeAt(base.length - 1) + (c - lo)));
        }
      });
    });
    return { map, codeBytes };
  }

  /* Glyph widths (1/1000 of the font size) from the font dictionary, so cell edges are measured, not guessed. */
  function numbersOf(objs, dict, key) {
    const m = new RegExp("/" + key + "(?![A-Za-z])\\s*(\\[[^\\]]*\\]|\\d+\\s+\\d+\\s+R)").exec(dict);
    if (!m) return null;
    let text = m[1];
    if (text[0] !== "[") {
      const o = objs.get(Number(text.match(/^\d+/)[0]));
      text = o ? o.dict : "";
    }
    return Array.from(text.matchAll(/-?\d+\.?\d*/g)).map((x) => parseFloat(x[0]));
  }

  function fontInfo(objs, fo) {
    const info = { bytes: 1, cmap: null, width: () => 500 };
    const uni = fo.dict.match(/\/ToUnicode\s+(\d+)\s+\d+\s+R/);
    const stream = uni && objs.get(Number(uni[1]));
    if (stream && stream.data) { info.cmap = parseCMap(latin1(stream.data)); info.bytes = info.cmap.codeBytes; }
    if (/\/Subtype\s*\/Type0/.test(fo.dict)) {
      if (!info.cmap) info.bytes = 2;
      const d = fo.dict.match(/\/DescendantFonts\s*(?:\[\s*)?(\d+)\s+\d+\s+R/);
      let desc = d && objs.get(Number(d[1]));
      if (desc && !/\/Subtype\s*\/CIDFontType/.test(desc.dict)) { // the array was a separate object
        const inner = desc.dict.match(/(\d+)\s+\d+\s+R/);
        desc = inner && objs.get(Number(inner[1]));
      }
      if (desc) {
        const dw = Number((desc.dict.match(/\/DW\s+(\d+\.?\d*)/) || [])[1]) || 1000;
        const wm = /\/W\s*(\[[\s\S]*\]|\d+\s+\d+\s+R)/.exec(desc.dict);
        const map = new Map();
        let wtext = wm ? wm[1] : "";
        if (wtext && wtext[0] !== "[") { const o = objs.get(Number(wtext.match(/^\d+/)[0])); wtext = o ? o.dict : ""; }
        const tk = pdfTokens(wtext.slice(wtext.indexOf("[") + 1));
        for (let i = 0; i < tk.length;) {
          if (tk[i].t === "num" && tk[i + 1] && tk[i + 1].t === "op" && tk[i + 1].v === "[") {
            let j = i + 2;
            let c = tk[i].v;
            while (j < tk.length && !(tk[j].t === "op" && tk[j].v === "]")) { if (tk[j].t === "num") { map.set(c, tk[j].v); c += 1; } j += 1; }
            i = j + 1;
          } else if (tk[i].t === "num" && tk[i + 1] && tk[i + 1].t === "num" && tk[i + 2] && tk[i + 2].t === "num") {
            for (let c = tk[i].v; c <= Math.min(tk[i + 1].v, tk[i].v + 65535); c += 1) map.set(c, tk[i + 2].v);
            i += 3;
          } else i += 1;
        }
        info.width = (code) => (map.has(code) ? map.get(code) : dw);
      }
    } else {
      const first = Number((fo.dict.match(/\/FirstChar\s+(\d+)/) || [])[1]) || 0;
      const widths = numbersOf(objs, fo.dict, "Widths");
      if (widths) info.width = (code) => (widths[code - first] !== undefined ? widths[code - first] : 500);
    }
    return info;
  }

  function pdfTokens(s) {
    const out = [];
    const stop = /[\s/\[\]<>()%]/;
    let i = 0;
    while (i < s.length) {
      const c = s[i];
      if (c <= " ") { i += 1; continue; }
      if (c === "%") { while (i < s.length && s[i] !== "\n" && s[i] !== "\r") i += 1; continue; }
      if (c === "(") {
        let depth = 1;
        let v = "";
        i += 1;
        while (i < s.length && depth > 0) {
          const ch = s[i];
          if (ch === "\\") {
            const nx = s[i + 1];
            if (/[0-7]/.test(nx)) { const oct = s.slice(i + 1, i + 4).match(/^[0-7]{1,3}/)[0]; v += String.fromCharCode(parseInt(oct, 8) & 255); i += 1 + oct.length; continue; }
            const esc = { n: "\n", r: "\r", t: "\t", b: "\b", f: "\f" }[nx];
            if (esc !== undefined) v += esc; else if (nx !== "\n" && nx !== "\r") v += nx;
            i += 2;
            continue;
          }
          if (ch === "(") depth += 1;
          if (ch === ")") { depth -= 1; if (depth === 0) { i += 1; break; } }
          v += ch;
          i += 1;
        }
        out.push({ t: "str", v });
      } else if (c === "<" && s[i + 1] === "<") { out.push({ t: "op", v: "<<" }); i += 2; }
      else if (c === ">" && s[i + 1] === ">") { out.push({ t: "op", v: ">>" }); i += 2; }
      else if (c === "<") {
        const j = s.indexOf(">", i);
        const hex = s.slice(i + 1, j < 0 ? s.length : j).replace(/\s/g, "");
        let v = "";
        for (let k = 0; k < hex.length; k += 2) v += String.fromCharCode(parseInt((hex.slice(k, k + 2) + "0").slice(0, 2), 16));
        out.push({ t: "str", v });
        i = j < 0 ? s.length : j + 1;
      } else if (c === "[" || c === "]") { out.push({ t: "op", v: c }); i += 1; }
      else if (c === "/") {
        let j = i + 1;
        while (j < s.length && !stop.test(s[j])) j += 1;
        out.push({ t: "name", v: s.slice(i + 1, j) });
        i = j;
      } else if (c === ")" || c === ">") { i += 1; }
      else {
        let j = i;
        while (j < s.length && !stop.test(s[j])) j += 1;
        const w = s.slice(i, j);
        out.push(/^[+-]?(\d+\.?\d*|\.\d+)$/.test(w) ? { t: "num", v: parseFloat(w) } : { t: "op", v: w });
        i = j;
      }
    }
    return out;
  }

  const mul = (m, n) => [
    m[0] * n[0] + m[1] * n[2], m[0] * n[1] + m[1] * n[3], m[2] * n[0] + m[3] * n[2], m[2] * n[1] + m[3] * n[3],
    m[4] * n[0] + m[5] * n[2] + n[4], m[4] * n[1] + m[5] * n[3] + n[5],
  ];

  /* Runs the drawing instructions of one page and returns every piece of text with its position. */
  function textItems(content, fonts) {
    const items = [];
    const stack = [];
    let ctm = [1, 0, 0, 1, 0, 0];
    let tm = [1, 0, 0, 1, 0, 0];
    let lm = tm;
    let font = null;
    let size = 1;
    let leading = 0;
    let args = [];
    const arrays = [];
    const decode = (str) => {
      let text = "";
      let w = 0;
      const n = font ? font.bytes : 1;
      for (let i = 0; i + n <= str.length; i += n) {
        let code = 0;
        for (let k = 0; k < n; k += 1) code = code * 256 + (str.charCodeAt(i + k) & 255);
        text += font && font.cmap ? (font.cmap.map.has(code) ? font.cmap.map.get(code) : "") : String.fromCharCode(code);
        w += font ? font.width(code) : 500;
      }
      return { text, w };
    };
    const put = (parts) => {
      let text = "";
      let advance = 0;
      parts.forEach((p) => {
        if (typeof p === "number") { advance -= (p / 1000) * size; if (p < -250) text += " "; } else { const r = decode(p); text += r.text; advance += (r.w / 1000) * size; }
      });
      if (text.trim() === "") { tm = mul([1, 0, 0, 1, advance, 0], tm); return; }
      const p = mul(tm, ctm);
      const scale = Math.hypot(tm[0], tm[1]) * Math.hypot(ctm[0], ctm[1]) || 1;
      items.push({ x: p[4], y: p[5], size: size * scale, text, width: advance * scale });
      tm = mul([1, 0, 0, 1, advance, 0], tm);
    };
    for (const tk of pdfTokens(content)) {
      if (tk.t === "op" && tk.v === "[") { arrays.push([]); continue; }
      if (tk.t === "op" && tk.v === "]") { const a = arrays.pop() || []; (arrays.length ? arrays[arrays.length - 1] : args).push({ t: "arr", v: a }); continue; }
      if (tk.t !== "op") { (arrays.length ? arrays[arrays.length - 1] : args).push(tk.t === "arr" ? tk : tk); continue; }
      const num = (i) => (args[i] && args[i].t === "num" ? args[i].v : 0);
      switch (tk.v) {
        case "q": stack.push(ctm); break;
        case "Q": ctm = stack.pop() || ctm; break;
        case "cm": ctm = mul([num(0), num(1), num(2), num(3), num(4), num(5)], ctm); break;
        case "BT": tm = [1, 0, 0, 1, 0, 0]; lm = tm; break;
        case "Tf": font = fonts[args[0] && args[0].v] || null; size = num(1) || size; break;
        case "TL": leading = num(0); break;
        case "Tm": tm = [num(0), num(1), num(2), num(3), num(4), num(5)]; lm = tm; break;
        case "Td": lm = mul([1, 0, 0, 1, num(0), num(1)], lm); tm = lm; break;
        case "TD": leading = -num(1); lm = mul([1, 0, 0, 1, num(0), num(1)], lm); tm = lm; break;
        case "T*": lm = mul([1, 0, 0, 1, 0, -leading], lm); tm = lm; break;
        case "Tj": if (args[0]) put([args[0].v]); break;
        case "'": lm = mul([1, 0, 0, 1, 0, -leading], lm); tm = lm; if (args[0]) put([args[0].v]); break;
        case "\"": lm = mul([1, 0, 0, 1, 0, -leading], lm); tm = lm; if (args[2]) put([args[2].v]); break;
        case "TJ": if (args[0] && args[0].t === "arr") put(args[0].v.map((a) => a.v)); break;
        default: break;
      }
      args = [];
    }
    return items;
  }

  /* Rows by height; every positioned piece of text is a cell. The header row fixes the columns, and a lone
     line just under a row (a wrapped cell) continues that row. */
  function tableFromItems(pages) {
    const lines = [];
    pages.forEach((items) => {
      const sorted = items.slice().sort((a, b) => b.y - a.y || a.x - b.x);
      let line = null;
      sorted.forEach((it) => {
        if (line && Math.abs(line.y - it.y) <= Math.max(2, 0.4 * it.size)) line.items.push(it);
        else { line = { y: it.y, items: [it], page: pages.indexOf(items) }; lines.push(line); }
      });
    });
    lines.forEach((l) => l.items.sort((a, b) => a.x - b.x));
    const multi = lines.filter((l) => l.items.length >= 2);
    if (!multi.length) return null;
    const freq = {};
    multi.forEach((l) => { freq[l.items.length] = (freq[l.items.length] || 0) + 1; });
    const modal = Object.keys(freq).map(Number).sort((a, b) => freq[b] - freq[a] || b - a)[0];
    const headerLine = multi.find((l) => l.items.length === modal);
    const header = headerLine.items;
    // A column runs from its header's left edge to the next header's. The piece of text is placed by its middle,
    // so left aligned text and right aligned numbers both land in the right column.
    const columnOf = (it) => {
      const middle = it.x + it.width / 2;
      let col = 0;
      header.forEach((h, i) => { if (h.x <= middle + 1) col = i; });
      return col;
    };
    const table = [header.map((h) => h.text.trim())];
    let previous = headerLine;
    lines.slice(lines.indexOf(headerLine) + 1).forEach((line) => {
      if (line.items.length >= 2) {
        const row = new Array(header.length).fill("");
        line.items.forEach((it) => {
          const c = columnOf(it);
          row[c] = (row[c] ? row[c] + " " : "") + it.text.trim();
        });
        if (row.join("|") === table[0].join("|")) return; // the header repeated on a new page
        table.push(row);
        previous = line;
      } else if (line.items.length === 1 && table.length > 1 && previous.page === line.page
        && previous.y - line.y <= 1.45 * line.items[0].size) {
        const row = table[table.length - 1];
        const c = columnOf(line.items[0]);
        row[c] = (row[c] ? row[c] + " " : "") + line.items[0].text.trim();
        previous = line;
      }
    });
    return table;
  }

  async function pdfItems(buffer) {
    const bytes = new Uint8Array(buffer);
    const objs = await readPdfObjects(bytes);
    const pages = pdfPages(objs);
    if (!pages.length) throw new Error("That PDF could not be read. Export the table as CSV or Excel instead.");
    const perPage = [];
    for (const num of pages) {
      const dict = objs.get(num).dict;
      let resources = pdfValue(objs, dict, "Resources");
      for (let up = dict, depth = 0; !resources && depth < 10; depth += 1) {
        const parent = (up.match(/\/Parent\s+(\d+)\s+\d+\s+R/) || [])[1];
        if (!parent || !objs.get(Number(parent))) break;
        up = objs.get(Number(parent)).dict;
        resources = pdfValue(objs, up, "Resources");
      }
      const fonts = {};
      const fontDict = resources ? pdfValue(objs, resources, "Font") : null;
      if (fontDict) {
        Array.from(fontDict.matchAll(/\/([^\s/\[\]<>()]+)\s+(\d+)\s+\d+\s+R/g)).forEach((f) => {
          const fo = objs.get(Number(f[2]));
          if (fo) fonts[f[1]] = fontInfo(objs, fo);
        });
      }
      const refs = (dict.match(/\/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)/) || [])[1] || "";
      const parts = [];
      for (const r of refs.matchAll(/(\d+)\s+\d+\s+R/g)) {
        const o = objs.get(Number(r[1]));
        if (o && o.data) parts.push(latin1(o.data));
      }
      perPage.push(textItems(parts.join("\n"), fonts));
    }
    return perPage;
  }

  async function tableFromPdf(buffer) {
    const perPage = await pdfItems(buffer);
    if (!perPage.some((p) => p.length)) throw new Error("This PDF has no text that can be read. It may be a scan or a picture of a table. Export the data as CSV or Excel instead.");
    const table = tableFromItems(perPage);
    if (!table) throw new Error("No table with column names was found in that PDF.");
    return table;
  }

  /* ---------- deciding what a file is, then reading it ---------- */

  const BY_EXTENSION = {
    csv: "text", tsv: "text", tab: "text", txt: "text", dat: "text", psv: "text",
    json: "json", jsonl: "json", ndjson: "json", geojson: "json",
    xml: "xml", html: "html", htm: "html", md: "markdown", markdown: "markdown",
    xlsx: "zip", xlsm: "zip", xltx: "zip", ods: "zip", docx: "zip", pptx: "zip", zip: "zip",
  };

  const UNSUPPORTED = {
    xls: "Old Excel (.xls) files cannot be read in the browser. In Excel choose Save As and pick Excel Workbook (.xlsx) or CSV.",
    doc: "Old Word (.doc) files cannot be read. Save the document as .docx first.",
    image: "Pictures and scans cannot be read as data. Type the table into a spreadsheet, or use a CSV or Excel file.",
    ppt: "Old PowerPoint (.ppt) files cannot be read. Save the presentation as .pptx first.",
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
    if (sig === "pdf") return { kind: "PDF", table: await tableFromPdf(buffer) };
    if (sig === "image" || IMAGES.includes(ext)) throw new Error(UNSUPPORTED.image);
    if (sig === "ole") throw new Error(UNSUPPORTED[ext === "doc" || ext === "ppt" ? ext : "xls"]);
    // The content decides. Many ".xls" exports are really HTML or text tables, so the extension alone never rejects a file.
    let kind = sig || (BY_EXTENSION[ext] === "zip" ? "text" : BY_EXTENSION[ext]) || sniff(bytes);
    if (kind === "zip") {
      const zip = openZip(buffer);
      if (zip.has("xl/workbook.xml")) return { kind: "Excel", table: await tableFromXlsx(zip) };
      if (zip.has("content.xml")) return { kind: "OpenDocument", table: await tableFromOds(zip) };
      if (zip.has("word/document.xml")) return { kind: "Word", table: await tableFromDocx(zip) };
      if (zip.has("ppt/presentation.xml")) return { kind: "PowerPoint", table: await tableFromPptx(zip) };
      throw new Error("That zip file does not contain an Excel, OpenDocument, Word or PowerPoint document.");
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

  const api = { parseBuffer, parseDelimited, tableFromJsonText, tableFromMarkdown, tableFromXml, tableFromHtml, openZip, tableFromXlsx, tableFromOds, tableFromDocx, tableFromPptx, tableFromPdf, pdfItems, sniff };
  root.StockFlowImport = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
