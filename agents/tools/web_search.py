"""Web search tool — DuckDuckGo without an API key, with fallbacks.

DuckDuckGo's HTML endpoints are unofficial and answer a rate-limited or
suspicious request with HTTP 202 and an empty body instead of an error, so a
"successful" response can carry zero results. Measured: the same query returned
10 results from /lite/ while /html/ answered 202. So the chain is:

    html endpoint -> lite endpoint -> the connected MCP search server (if any)

and a request that comes back empty moves on to the next source instead of
repeating itself. If everything is empty, the message says so plainly — the
model must not present "the search engine blocked me" as "there is no such thing".

If this proves unreliable in daily use, swap the implementation for a real
search API (Brave Search API, Tavily, or a self-hosted SearXNG instance)
without touching anything that calls this tool — the @tool contract stays the same.
"""
import time

import requests
from bs4 import BeautifulSoup

from .registry import _REGISTRY, tool

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}

HTML_URL = "https://html.duckduckgo.com/html/"
LITE_URL = "https://lite.duckduckgo.com/lite/"
MAX_RETRIES = 3
MAX_RESULTS = 5


def _format(title: str, link: str, snippet: str) -> str:
    return f"- {title}\n  {link}\n  {snippet}"


def _parse_html(page: str) -> list[str]:
    soup = BeautifulSoup(page, "html.parser")
    results = []
    for r in soup.select(".result")[:MAX_RESULTS]:
        title_el = r.select_one(".result__title")
        if not title_el:
            continue
        snippet_el = r.select_one(".result__snippet")
        link_el = r.select_one(".result__url")
        results.append(_format(title_el.get_text(strip=True),
                               link_el.get_text(strip=True) if link_el else "",
                               snippet_el.get_text(strip=True) if snippet_el else ""))
    return results


def _parse_lite(page: str) -> list[str]:
    soup = BeautifulSoup(page, "html.parser")
    links = soup.select("a.result-link")
    snippets = soup.select("td.result-snippet")
    return [_format(a.get_text(strip=True), a.get("href", ""),
                    snippets[i].get_text(strip=True) if i < len(snippets) else "")
            for i, a in enumerate(links[:MAX_RESULTS])]


def _fetch(url: str, query: str) -> str | None:
    """The page text, or None when the engine answered but didn't serve results (HTTP 202 etc.)."""
    with requests.Session() as session:
        resp = session.post(url, data={"q": query}, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    return resp.text if getattr(resp, "status_code", 200) == 200 else None


def _mcp_fallback(query: str) -> str | None:
    entry = _REGISTRY.get("mcp_search_search")
    if not entry:
        return None
    required = entry["schema"]["function"]["parameters"].get("required") or ["query"]
    try:
        out = str(entry["fn"](**{required[0]: query})).strip()
    except Exception:  # a broken fallback must not break the tool
        return None
    return out or None


@tool(
    "Search the public web for a query and return the top result titles, links and snippets.",
    keywords=("ابحث", "بحث", "الانترنت", "الإنترنت", "انترنت", "اخبار", "أخبار", "آخر", "اخر", "سعر", "أسعار",
              "اسعار", "طقس", "درجة الحرارة", "search", "google", "news", "latest", "price", "weather", "look up"),
)
def web_search(query: str) -> str:
    # Tool errors must never raise past this function — the agent loop feeds
    # the return value straight back to the model, so a network failure has
    # to come back as a readable string it can react to (retry, tell the
    # user, etc.) instead of crashing the whole turn.
    last_error = None
    for url, parse in ((HTML_URL, _parse_html), (LITE_URL, _parse_lite)):
        for attempt in range(MAX_RETRIES):
            try:
                page = _fetch(url, query)
            except requests.RequestException as e:
                last_error = e
                time.sleep(0.5 * (attempt + 1))
                continue
            results = parse(page) if page else []
            if results:
                return "\n".join(results)
            break  # answered but empty/blocked: try the next source, not the same request again

    fallback = _mcp_fallback(query)
    if fallback:
        return fallback
    if last_error is not None:
        return f"Search failed after {MAX_RETRIES} attempts (network/connection error): {last_error}"
    return ("No results found. The search engines returned nothing — this can mean a rate limit rather than "
            "that nothing exists; try rephrasing the query.")
