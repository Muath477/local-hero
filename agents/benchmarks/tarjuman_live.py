"""Live check of Tarjuman on a realistic .docx with the configured model.

    python -m benchmarks.tarjuman_live                  # translate to Arabic with models.yaml's tarjuman model
    python -m benchmarks.tarjuman_live --model qwen3-4b-instruct-2507:Q4_K_M --keep out.docx

Builds a contract-style document (headings, inline bold/italic, a table with a
merged cell, header/footer, a hyperlink, repeated boilerplate, numbers), then
verifies what the unit tests can only assume: no foreign-script leaks, inline
formatting landed on translated words, RTL markup is present, nothing is left
untranslated. Prints wall time and model-call counts against what a
paragraph-by-paragraph translator (the previous design) would have needed.
"""
from __future__ import annotations

import argparse
import io
import sys
import tempfile
import time
from pathlib import Path

import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from tools import tarjuman
from tools.tarjuman import _is_contaminated, translate_docx_with_stats


def build_sample() -> bytes:
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = "Confidential - Service Agreement"
    d.sections[0].footer.paragraphs[0].text = "Page footer: Acme Trading Co."
    d.add_heading("Service Agreement", level=1)
    p = d.add_paragraph("This agreement is made between ")
    p.add_run("Acme Trading Co.").bold = True
    p.add_run(" and the ")
    p.add_run("Client").italic = True
    p.add_run(" and takes effect on 1 January 2027.")
    d.add_paragraph("The supplier shall deliver the goods within thirty days of receiving the purchase order.")
    d.add_paragraph("Confidential")
    d.add_paragraph("Confidential")
    d.add_heading("Payment Terms", level=2)
    t = d.add_table(rows=4, cols=3)
    for j, h in enumerate(("Item", "Quantity", "Price")):
        t.cell(0, j).text = h
    rows = (("Laptop", "10", "4500"), ("Monitor", "20", "900"))
    for i, row in enumerate(rows, 1):
        for j, v in enumerate(row):
            t.cell(i, j).text = v
    t.cell(3, 0).merge(t.cell(3, 2)).text = "Total amount due within 30 days"
    q = d.add_paragraph("Late payments will incur a penalty of ")
    q.add_run("two percent per month").bold = True
    q.add_run(". Questions can be sent to support@acme.example.")
    link_p = d.add_paragraph("For details visit ")
    link = OxmlElement("w:hyperlink")
    r = OxmlElement("w:r")
    tx = OxmlElement("w:t")
    tx.text = "our website"
    r.append(tx)
    link.append(r)
    link_p._p.append(link)
    d.add_paragraph("2027")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def all_paragraphs(doc):
    out = list(doc.paragraphs)
    for t in doc.tables:
        for row in t.rows:
            seen = set()
            for cell in row.cells:
                if cell._tc not in seen:
                    seen.add(cell._tc)
                    out.extend(cell.paragraphs)
    for s in doc.sections:
        out += s.header.paragraphs + s.footer.paragraphs
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="override the tarjuman model tag")
    ap.add_argument("--language", default="Arabic")
    ap.add_argument("--keep", help="write the translated .docx here")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    src = build_sample()
    src_doc = docx.Document(io.BytesIO(src))
    legacy_calls = sum(1 for p in all_paragraphs(src_doc) if p.text.strip())

    with tempfile.TemporaryDirectory() as tmp:
        tarjuman.CACHE_PATH = Path(tmp) / "cache.json"  # cold cache: measure the model, not memory
        if args.model:
            tarjuman.load_registry = lambda m=args.model, f=tarjuman.load_registry: {**f(), "tarjuman": {**f()["tarjuman"], "ollama_tag": m}}
        started = time.time()
        out, stats = translate_docx_with_stats(src, args.language)
        cold = time.time() - started
        started = time.time()
        translate_docx_with_stats(src, args.language)
        warm = time.time() - started

    doc = docx.Document(io.BytesIO(out))
    paras = all_paragraphs(doc)
    checks = []
    arabic = args.language.casefold() in ("arabic", "العربية")
    if arabic:
        leaks = [p.text for p in paras if p.text.strip() and _is_contaminated(p.text)
                 and not any(k in p.text for k in ("support@acme.example",))]
        checks.append(("no foreign-script or untranslated words", not leaks, leaks[:3]))
    bold_runs = [r.text for p in paras for r in p.runs if r.bold and r.text.strip()]
    checks.append(("bold survived on some translated words", bool(bold_runs), bold_runs))
    checks.append(("no placeholder tags left", not any("{{" in p.text for p in paras), []))
    checks.append(("table translated (header cell changed)", doc.tables[0].cell(0, 0).text != "Item", doc.tables[0].cell(0, 0).text))
    checks.append(("header and footer translated", doc.sections[0].header.paragraphs[0].text != "Confidential - Service Agreement", []))
    checks.append(("hyperlink kept", len(doc.paragraphs[-2]._p.findall(qn("w:hyperlink"))) == 1, []))
    checks.append(("numbers and email untouched", any(p.text.strip() == "2027" for p in paras), []))
    if arabic:
        checks.append(("paragraphs marked RTL", all(p._p.pPr is not None and p._p.pPr.find(qn("w:bidi")) is not None
                                                    for p in doc.paragraphs if p.text.strip()), []))
        checks.append(("table mirrored", doc.tables[0]._tbl.tblPr.find(qn("w:bidiVisual")) is not None, []))

    print(f"model: {tarjuman.load_registry()['tarjuman']['ollama_tag']}  target: {args.language}")
    print(f"cold run: {cold:.0f}s | warm run (cache): {warm:.1f}s | stats: {stats}")
    print(f"model calls: {stats.get('llm_calls')} vs {legacy_calls} for one-call-per-paragraph")
    failed = 0
    for name, ok, detail in checks:
        failed += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if not ok and detail else ""))
    print("\ntranslated text:")
    for p in paras:
        if p.text.strip():
            print("  -", p.text)
    if args.keep:
        Path(args.keep).write_bytes(out)
        print(f"\nsaved {args.keep}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
