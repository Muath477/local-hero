"""Web search tool — uses DuckDuckGo's HTML endpoint (no API key needed).

Known limitation: this endpoint is unofficial. On some networks (security
software doing TLS inspection, or DDG's own rate limiting) requests get
reset intermittently — retried below with backoff, but if this proves
unreliable in daily use, swap the implementation for a real search API
(Brave Search API, Tavily, or a self-hosted SearXNG instance) without
touching anything that calls this tool — the @tool contract stays the same.
"""
import time

import requests
from bs4 import BeautifulSoup

from .registry import tool

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}


MAX_RETRIES = 3


@tool("Search the public web for a query and return the top result titles, links and snippets.")
def web_search(query: str) -> str:
    # Tool errors must never raise past this function — the agent loop feeds
    # the return value straight back to the model, so a network failure has
    # to come back as a readable string it can react to (retry, tell the
    # user, etc.) instead of crashing the whole turn.
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            with requests.Session() as session:
                resp = session.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query},
                    headers=HEADERS,
                    timeout=15,
                )
                resp.raise_for_status()
            break
        except requests.RequestException as e:
            last_error = e
            time.sleep(0.5 * (attempt + 1))
    else:
        return f"Search failed after {MAX_RETRIES} attempts (network/connection error): {last_error}"

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for r in soup.select(".result")[:5]:
        title_el = r.select_one(".result__title")
        snippet_el = r.select_one(".result__snippet")
        link_el = r.select_one(".result__url")
        if not title_el:
            continue
        results.append(
            f"- {title_el.get_text(strip=True)}\n"
            f"  {link_el.get_text(strip=True) if link_el else ''}\n"
            f"  {snippet_el.get_text(strip=True) if snippet_el else ''}"
        )
    return "\n".join(results) if results else "No results found."
