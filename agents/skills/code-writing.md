---
name: code-writing
description: Conventions for writing new functions and scripts
roles: coder
triggers: اكتب دالة, اكتب لي دالة, اكتب سكربت, اكتب كود, كلاس, write a function, write a script, write a class, write code, implement
---
When writing new code:
- Use EXACTLY the function, class and argument names the user gave.
- Standard library only unless the user names a library.
- Handle empty or invalid input sensibly (return a neutral value or raise a clear error).
- Put the code in ONE fenced block with the language tag, then at most two lines of explanation in the user's language.
- Plain ASCII operators only (* not ×, ** for powers). Never leave placeholders like "..." inside code.
