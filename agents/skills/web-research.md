---
name: web-research
description: Search first, answer only from results, list sources
roles: tool_caller
triggers: ابحث, بحث, أخبار, اخبار, سعر, أسعار, طقس, درجة الحرارة, search, news, price, weather, latest, look up
---
For anything that depends on current information:
- Call web_search FIRST; never answer live facts (prices, news, weather, scores) from memory.
- After the tool returns, answer in the user's language in 2-4 sentences using ONLY what the results say, then list the source URLs.
- If results are empty or contradict each other, say so plainly instead of choosing one.
- Use the calculator tool for any arithmetic on the results.
