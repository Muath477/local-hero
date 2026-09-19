"""Built-in tools beyond web search and file reading.

Deliberately NOT here: a "run Python" tool. A substring blocklist is not a
sandbox (attribute tricks get around it), and an agent that reads web pages
can be steered by text inside them — code execution needs real isolation
(container / restricted user), not a regex.
"""
from __future__ import annotations

import ast
import math
import operator
import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from . import file_reader
from .registry import tool

# ── calculator ──────────────────────────────────────────────────────────────

_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {"sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max, "sin": math.sin,
              "cos": math.cos, "tan": math.tan, "log": math.log, "log10": math.log10, "exp": math.exp,
              "floor": math.floor, "ceil": math.ceil}
_CONSTANTS = {"pi": math.pi, "e": math.e}
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_SYMBOLS = str.maketrans({"×": "*", "÷": "/", "^": "**", "،": ",", "−": "-", "٫": "."})
MAX_EXPRESSION = 200


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 1000 or (isinstance(left, int) and left.bit_length() * abs(right) > 20000)):
            raise ValueError("exponent too large")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS and not node.keywords:
        return _FUNCTIONS[node.func.id](*[_eval(a) for a in node.args])
    raise ValueError("unsupported expression")


@tool(
    "Evaluate an arithmetic expression exactly (+ - * / // % ** parentheses, sqrt, round, min, max, pi). "
    "Use this for any calculation instead of computing in your head.",
    keywords=("احسب", "حساب", "كم يساوي", "ناتج", "جمع", "ضرب", "قسمة", "نسبة", "calculate", "compute", "sum of", "how much is"),
    params={"expression": "arithmetic expression, e.g. 12.5 * (3 + 4)"},
)
def calculator(expression: str) -> str:
    text = expression.translate(_DIGITS).translate(_SYMBOLS).strip()
    if not text or len(text) > MAX_EXPRESSION:
        return "Error: expression is empty or too long."
    try:
        result = _eval(ast.parse(text, mode="eval"))
    except ZeroDivisionError:
        return "Error: division by zero."
    except (ValueError, SyntaxError, TypeError, OverflowError) as e:
        return f"Error: can't evaluate '{expression}' ({e}). Use only numbers, + - * / ** and parentheses."
    return str(result) if isinstance(result, int) else format(result, ".12g")


# ── date and time ───────────────────────────────────────────────────────────

_WEEKDAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


@tool(
    "Get the current date, weekday and time in a timezone (default Asia/Riyadh). Use for 'what day/time is it' questions.",
    keywords=("الساعة", "الوقت", "التاريخ", "أي يوم", "اي يوم", "what time", "what day", "today's date", "current time", "date today"),
    params={"tz": "IANA timezone name, e.g. Asia/Riyadh, Europe/London, UTC"},
)
def get_datetime(tz: str = "Asia/Riyadh") -> str:
    try:
        now = datetime.now(ZoneInfo(tz.strip() or "Asia/Riyadh"))
    except (ZoneInfoNotFoundError, ValueError):
        return f"Error: unknown timezone '{tz}'. Use an IANA name such as Asia/Riyadh or UTC."
    offset = now.strftime("%z")
    return (f"{now:%Y-%m-%d %H:%M} ({now:%A} / {_WEEKDAYS_AR[now.weekday()]}), "
            f"{tz}, UTC{offset[:3]}:{offset[3:]}")


# ── uploaded files ──────────────────────────────────────────────────────────

def _upload_path(filename: str):
    uploads = file_reader.UPLOADS_DIR.resolve()
    path = (uploads / filename).resolve()
    if uploads not in path.parents:
        raise ValueError("access outside the uploads directory is not allowed")
    if not path.is_file():
        raise ValueError(f"'{filename}' not found in uploads")
    return path


@tool(
    "List the names of the files the user has uploaded. Use ONLY when the user asks what files exist or the "
    "filename is unknown — if a filename is given, call read_uploaded_file directly.",
    keywords=("الملفات", "ملفاتي", "وش رفعت", "ايش رفعت", "المرفقات", "list files", "uploaded files", "what files", "my files"),
)
def list_uploaded_files() -> str:
    uploads = file_reader.UPLOADS_DIR
    files = sorted(p for p in uploads.glob("*") if p.is_file() and not p.name.startswith(".")) if uploads.exists() else []
    if not files:
        return "No files uploaded."
    return "\n".join(f"- {p.name} ({p.stat().st_size} bytes)" for p in files)


# ── translation (Tarjuman engine) ───────────────────────────────────────────

MAX_TEXT_CHARS = 5000


@tool(
    "Translate a piece of text into another language with the dedicated translation model. "
    "Prefer this over translating yourself.",
    keywords=("ترجم", "ترجمة", "translate", "translation", "بالعربي", "للعربية", "للإنجليزية", "للانجليزي", "into english", "into arabic"),
    params={"text": "the text to translate", "target_language": "e.g. Arabic, English, French"},
)
def translate_text(text: str, target_language: str = "Arabic") -> str:
    from .tarjuman import TranslationCache, Translator

    if len(text) > MAX_TEXT_CHARS:
        return f"Error: text is longer than {MAX_TEXT_CHARS} characters — upload it as a .docx and use translate_document."
    cache = TranslationCache()
    out = Translator(target_language, cache=cache)([text.strip()])[0]
    cache.save()
    return out


@tool(
    "Translate an uploaded .docx file into another language, keeping its formatting and tables. "
    "Saves a new file in uploads and returns its name. Slow (minutes) for long documents.",
    keywords=("ترجم الملف", "ترجمة الملف", "ترجم المستند", "ترجم الوثيقة", "translate the file", "translate the document", "translate this docx", "docx"),
    params={"filename": "name of the uploaded .docx", "target_language": "e.g. Arabic, English, French"},
)
def translate_document(filename: str, target_language: str = "Arabic") -> str:
    from .tarjuman import translate_docx_with_stats

    try:
        path = _upload_path(filename)
    except ValueError as e:
        return f"Error: {e}."
    if path.suffix.lower() != ".docx":
        return "Error: only .docx files can be translated with formatting preserved."
    data, stats = translate_docx_with_stats(path.read_bytes(), target_language)
    slug = re.sub(r"\W+", "_", target_language.strip()).strip("_").lower() or "translated"
    out = path.with_name(f"{path.stem}.{slug}.docx")
    out.write_bytes(data)
    return (f"Translated '{path.name}' -> '{out.name}' ({stats.get('segments', 0)} segments, "
            f"{stats.get('llm_calls', 0)} model calls, {stats.get('cached', 0)} from cache).")


# ── Hugging Face lookup ─────────────────────────────────────────────────────

@tool(
    "Search Hugging Face for models by name or task and return the most downloaded matches with size hints.",
    keywords=("موديل", "موديلات", "نموذج", "hugging face", "huggingface", "هقنق", "gguf", "model search", "find a model"),
    params={"query": "search words, e.g. 'qwen3 gguf' or 'arabic embedding'"},
)
def hf_model_search(query: str) -> str:
    try:
        resp = requests.get("https://huggingface.co/api/models",
                            params={"search": query, "sort": "downloads", "direction": -1, "limit": 5}, timeout=15)
        resp.raise_for_status()
        models = resp.json()
    except (requests.RequestException, ValueError) as e:
        return f"Hugging Face search failed: {e}"
    if not models:
        return "No models found."
    return "\n".join(
        f"- {m['id']} — {m.get('downloads', 0):,} downloads, {m.get('likes', 0)} likes"
        + (f", {m['pipeline_tag']}" if m.get("pipeline_tag") else "")
        for m in models
    )
