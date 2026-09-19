---
name: document-translation
description: Route file and text translation to the dedicated Tarjuman tools
roles: tool_caller
triggers: ترجم الملف, ترجمة الملف, ترجم المستند, ترجم لي, ترجم هذا, translate the file, translate the document, translate this, docx
---
For translation requests:
- An uploaded file: call translate_document(filename, target_language). It keeps tables and formatting and saves a NEW file in uploads — tell the user its name, and that long documents take minutes.
- Only .docx keeps its formatting; for other file types say so instead of trying.
- Short pasted text: call translate_text(text, target_language) and return its output unchanged.
- If the filename is unknown, call list_uploaded_files first.
