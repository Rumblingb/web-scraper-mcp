"""
Web Scraper MCP Server
======================
Provides web scraping capabilities via MCP tools.
Uses httpx for HTTP requests and regex for HTML parsing (zero external HTML parsing dependencies).

Pricing: $19/mo — https://buy.stripe.com/dRm6oJ4Hd2Jugek0wz1oI0m
"""

import re
import json
import asyncio
from urllib.parse import urljoin, urlparse
from typing import Optional

import httpx
from mcp.server import Server
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types

# ─── Constants ───────────────────────────────────────────────────────────────

SELF_CLOSING_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

DEFAULT_TIMEOUT = 30

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; WebScraperMCP/1.0; "
        "+https://github.com/nousresearch/web-scraper-mcp)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ─── HTML Entity Decoding ────────────────────────────────────────────────────

_HTML_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&#x27;": "'",
    "&#x2F;": "/",
    "&nbsp;": " ",
    "&mdash;": "—",
    "&ndash;": "–",
    "&hellip;": "…",
    "&copy;": "©",
    "&reg;": "®",
    "&trade;": "™",
    "&bull;": "•",
    "&middot;": "·",
}


def decode_entities(text: str) -> str:
    """Decode common HTML entities in a string."""
    for entity, char in _HTML_ENTITIES.items():
        text = text.replace(entity, char)
    # Decode numeric entities (&#NNNN;)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    # Decode hex entities (&#xNNNN;)
    text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), text)
    return text


# ─── Text Extraction ─────────────────────────────────────────────────────────

def extract_title(html: str) -> Optional[str]:
    """Extract the <title> tag content."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if m:
        return decode_entities(m.group(1).strip())
    return None


def extract_meta_description(html: str) -> Optional[str]:
    """Extract the meta description content."""
    patterns = [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']',
        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+name=["\']description["\']',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            return decode_entities(m.group(1).strip())
    return None


def extract_text_content(html: str) -> str:
    """Strip all HTML tags and return visible text."""
    # Remove script and style blocks
    text = re.sub(
        r"<(script|style|noscript)[^>]*>.*?</\1\s*>",
        "",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode entities
    text = decode_entities(text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ─── Link Extraction ─────────────────────────────────────────────────────────

def extract_links(html: str, base_url: str) -> list[dict]:
    """Extract all hyperlinks from HTML."""
    links = []
    seen = set()
    for m in re.finditer(
        r'<a[^>]+href=["\']([^"\']*)["\']',
        html,
        re.IGNORECASE,
    ):
        href = m.group(1).strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        abs_url = urljoin(base_url, href)
        if abs_url in seen:
            continue
        seen.add(abs_url)

        # Extract link text from context
        remaining = html[m.end() :]
        text_m = re.match(r"[^<]*(?:<(?!a\b)[^>]*>)*([^<]*)", remaining, re.IGNORECASE)
        text = ""
        if text_m:
            raw = re.sub(r"<[^>]+>", " ", text_m.group(1)).strip()
            text = decode_entities(raw)

        links.append({
            "href": href,
            "absolute_url": abs_url,
            "text": text,
        })
    return links


def extract_links_filtered(
    html: str, base_url: str, domain_filter: str
) -> list[dict]:
    """Extract links filtered by domain."""
    links = extract_links(html, base_url)
    domain_lower = domain_filter.lower()
    return [
        link
        for link in links
        if domain_lower in urlparse(link["absolute_url"]).netloc.lower()
    ]


# ─── Image Extraction ────────────────────────────────────────────────────────

def extract_images(html: str, base_url: str) -> list[dict]:
    """Extract all images from HTML."""
    images = []
    seen = set()
    for m in re.finditer(
        r'<img[^>]+src=["\']([^"\']*)["\']',
        html,
        re.IGNORECASE,
    ):
        src = m.group(1).strip()
        if not src:
            continue
        abs_url = urljoin(base_url, src)
        if abs_url in seen:
            continue
        seen.add(abs_url)

        tag = html[m.start() : m.end()]
        alt_m = re.search(r'alt=["\']([^"\']*)["\']', tag, re.IGNORECASE)
        alt = decode_entities(alt_m.group(1)) if alt_m else ""

        images.append({
            "src": src,
            "absolute_url": abs_url,
            "alt": alt,
        })
    return images


# ─── Email Extraction ────────────────────────────────────────────────────────

EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)

MAILTO_PATTERN = re.compile(
    r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
    re.IGNORECASE,
)


def extract_emails_from_text(text: str) -> list[str]:
    """Extract email addresses from plain text."""
    seen = set()
    for m in EMAIL_PATTERN.finditer(text):
        email = m.group()
        # Avoid false positives from encoded content
        if "@" in email and not email.startswith("http"):
            seen.add(email)
    return sorted(seen)


def extract_mailto_emails(html: str) -> list[str]:
    """Extract emails from mailto: links."""
    seen = set()
    for m in MAILTO_PATTERN.finditer(html):
        seen.add(m.group(1))
    return sorted(seen)


# ─── CSS Selector Utilities ──────────────────────────────────────────────────

class _ParsedSelector:
    """Represents a parsed simple CSS selector component."""

    __slots__ = ("tag", "classes", "id_", "attrs", "has_attr")

    def __init__(
        self,
        tag: Optional[str] = None,
        classes: Optional[set] = None,
        id_: Optional[str] = None,
        attrs: Optional[dict] = None,
        has_attr: Optional[str] = None,
    ):
        self.tag = tag
        self.classes = classes or set()
        self.id_ = id_
        self.attrs = attrs or {}
        self.has_attr = has_attr


def _parse_simple_selector(part: str) -> _ParsedSelector:
    """Parse a simple CSS selector like 'div.foo#bar[data-x=y]'."""
    tag = None
    classes: set[str] = set()
    id_ = None
    attrs: dict[str, str] = {}
    has_attr = None

    # Tag name (must be at the start)
    tm = re.match(r"^[a-zA-Z][a-zA-Z0-9]*", part)
    if tm:
        tag = tm.group()

    # Classes
    for m in re.finditer(r"\.([a-zA-Z0-9_-]+)", part):
        classes.add(m.group(1))

    # ID
    im = re.search(r"#([a-zA-Z0-9_-]+)", part)
    if im:
        id_ = im.group(1)

    # Attributes [attr] or [attr=value] or [attr~=value]
    for m in re.finditer(r"\[([a-zA-Z0-9_-]+)([~|^$*]?=([^\]]*))?\]", part):
        attr_name = m.group(1)
        attr_value_raw = m.group(3)  # might be None if just [attr]
        if attr_value_raw is not None:
            attr_value = attr_value_raw.strip("\"'")
            attrs[attr_name] = attr_value
        else:
            has_attr = attr_name

    return _ParsedSelector(
        tag=tag, classes=classes, id_=id_, attrs=attrs, has_attr=has_attr
    )


def _parse_attributes(attrs_str: str) -> dict[str, str]:
    """Parse key="value" key='value' key ... from a tag's attribute string."""
    attrs: dict[str, str] = {}
    for m in re.finditer(
        r"""([a-zA-Z_:][a-zA-Z0-9_:.\-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?""",
        attrs_str,
    ):
        name = m.group(1)
        value = m.group(2) or m.group(3) or m.group(4) or ""
        attrs[name] = value
    return attrs


def _element_matches(attrs: dict, tag: str, sel: _ParsedSelector) -> bool:
    """Check if an element matches the parsed selector."""
    if sel.tag and tag.lower() != sel.tag.lower():
        return False
    if sel.classes:
        elem_classes = set(attrs.get("class", "").split())
        if not sel.classes.issubset(elem_classes):
            return False
    if sel.id_ and attrs.get("id", "") != sel.id_:
        return False
    for aname, avalue in sel.attrs.items():
        if attrs.get(aname, None) != avalue:
            return False
    if sel.has_attr and sel.has_attr not in attrs:
        return False
    return True


def _extract_element_html(
    html: str, start: int, tag: str
) -> tuple[str, str, str]:
    """
    Given HTML and the start position of an opening <tag ...>, return
    (inner_html, full_element_html, text_content).
    """
    rest = html[start:]

    # Find end of opening tag
    oe = re.search(r">", rest)
    if not oe:
        return ("", rest, "")
    open_end = oe.end()

    # Self-closing (XHTML style: <tag ... />  or  HTML5 void elements)
    if tag.lower() in SELF_CLOSING_TAGS or rest[oe.start() - 1] == "/":
        full = rest[:open_end]
        return ("", full, "")

    open_pat = re.compile(rf"<{re.escape(tag)}[\s>]", re.IGNORECASE)
    close_pat = re.compile(rf"</{re.escape(tag)}\s*>", re.IGNORECASE)

    depth = 1
    pos = open_end

    while pos < len(rest) and depth > 0:
        nx_open = open_pat.search(rest, pos)
        nx_close = close_pat.search(rest, pos)

        if nx_close and (not nx_open or nx_close.start() < nx_open.start()):
            depth -= 1
            if depth == 0:
                inner = rest[open_end : nx_close.start()]
                full = rest[: nx_close.end()]
                text = _extract_element_text(inner)
                return (inner, full, text)
            pos = nx_close.end()
        elif nx_open:
            depth += 1
            pos = nx_open.end()
        else:
            # No more closing tags — treat rest as content
            break

    inner = rest[open_end:]
    text = _extract_element_text(inner)
    return (inner, rest, text)


def _extract_element_text(html: str) -> str:
    """Extract visible text from a fragment of inner HTML."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return decode_entities(text)


# Tags to search when no tag is specified in the selector
_COMMON_TAGS = (
    "div p span a h1 h2 h3 h4 h5 h6 ul ol li table tr td th "
    "section article header footer nav main aside "
    "form input button select textarea label fieldset legend "
    "figure figcaption blockquote pre code em strong b i u "
    "details summary dl dt dd".split()
)


def find_elements_by_selector(
    html: str, selector: str, base_url: str
) -> list[dict]:
    """
    Find HTML elements matching a CSS selector and return structured data.
    Supports: tag, .class, #id, [attr], [attr=value], and combinations.
    """
    parts = selector.strip().split()
    parsed = _parse_simple_selector(parts[-1])  # simple descendant support

    tags = [parsed.tag] if parsed.tag else _COMMON_TAGS
    results: list[dict] = []

    for tag in tags:
        pattern = re.compile(rf"<{re.escape(tag)}([^>]*)>", re.IGNORECASE)
        for m in pattern.finditer(html):
            attrs_str = m.group(1).strip()
            attrs = _parse_attributes(attrs_str)

            if not _element_matches(attrs, tag, parsed):
                continue

            inner, full, text = _extract_element_html(html, m.start(), tag)

            info: dict = {
                "tag": tag,
                "id": attrs.get("id", ""),
                "class": attrs.get("class", ""),
                "attributes": attrs,
                "text": text,
            }

            # Extract nested links / images
            info["links"] = (
                [{"href": attrs.get("href", ""),
                  "absolute_url": urljoin(base_url, attrs.get("href", "")),
                  "text": text}]
                if tag == "a"
                else extract_links(full, base_url)
            )
            info["images"] = extract_images(full, base_url)

            # Include raw html snippet for reference (truncated if huge)
            if len(full) > 10000:
                info["html_snippet"] = full[:10000] + "..."
            else:
                info["html"] = full

            results.append(info)

    return results


# ─── HTTP Client ─────────────────────────────────────────────────────────────

async def fetch_url(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Fetch a URL and return the raw HTML text."""
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout
    ) as client:
        resp = await client.get(url, headers=DEFAULT_HEADERS)
        resp.raise_for_status()
        return resp.text


# ─── MCP Server Definition ───────────────────────────────────────────────────

server = Server("web-scraper")


# ── Tool declarations ────────────────────────────────────────────────────────

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="scrape_url",
            description=(
                "Fetch a single URL and extract structured content. "
                "Returns title, meta description, visible text, all links, and all images. "
                "If a CSS selector is provided (e.g. 'div.content', 'h2', '.article-body'), "
                "returns matching elements with their text, attributes, links, and images."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to scrape",
                    },
                    "selector": {
                        "type": "string",
                        "description": (
                            "Optional CSS selector. Supports tag, .class, #id, "
                            "[attr], [attr=value], and combinations."
                        ),
                    },
                },
                "required": ["url"],
            },
        ),
        types.Tool(
            name="scrape_urls",
            description=(
                "Batch scrape multiple URLs concurrently. "
                "Returns title, text snippet, link count, and image count for each URL."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of URLs to scrape",
                    }
                },
                "required": ["urls"],
            },
        ),
        types.Tool(
            name="extract_emails",
            description=(
                "Extract all email addresses found on a web page. "
                "Scans both visible text and mailto: links."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to scan for email addresses",
                    }
                },
                "required": ["url"],
            },
        ),
        types.Tool(
            name="extract_links",
            description=(
                "Extract all hyperlinks from a web page. "
                "Optionally filter by domain (substring match on the hostname)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to extract links from",
                    },
                    "domain_filter": {
                        "type": "string",
                        "description": (
                            "Optional domain filter. Only links whose hostname "
                            "contains this string are returned "
                            "(e.g. 'example.com', 'docs.python.org')."
                        ),
                    },
                },
                "required": ["url"],
            },
        ),
    ]


# ── Tool call handler ────────────────────────────────────────────────────────

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    try:
        if name == "scrape_url":
            return await _tool_scrape_url(arguments)
        elif name == "scrape_urls":
            return await _tool_scrape_urls(arguments)
        elif name == "extract_emails":
            return await _tool_extract_emails(arguments)
        elif name == "extract_links":
            return await _tool_extract_links(arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    except Exception as exc:
        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "error": str(exc),
                        "tool": name,
                        "arguments": arguments,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
            )
        ]


# ── Tool implementations ─────────────────────────────────────────────────────

async def _tool_scrape_url(args: dict) -> list[types.TextContent]:
    url = args["url"]
    selector = args.get("selector")

    html = await fetch_url(url)

    if selector:
        elements = find_elements_by_selector(html, selector, url)
        result = {
            "url": url,
            "selector": selector,
            "match_count": len(elements),
            "elements": elements,
        }
    else:
        result = {
            "url": url,
            "title": extract_title(html),
            "meta_description": extract_meta_description(html),
            "text_content": extract_text_content(html),
            "links": extract_links(html, url),
            "images": extract_images(html, url),
        }

    return [
        types.TextContent(
            type="text",
            text=json.dumps(result, indent=2, ensure_ascii=False),
        )
    ]


async def _tool_scrape_urls(args: dict) -> list[types.TextContent]:
    urls: list[str] = args["urls"]

    async def fetch_one(u: str) -> dict:
        try:
            html = await fetch_url(u)
            return {
                "url": u,
                "success": True,
                "title": extract_title(html),
                "meta_description": extract_meta_description(html),
                "text_snippet": extract_text_content(html)[:500],
                "link_count": len(extract_links(html, u)),
                "image_count": len(extract_images(html, u)),
            }
        except Exception as e:
            return {"url": u, "success": False, "error": str(e)}

    results = await asyncio.gather(*[fetch_one(u) for u in urls])

    return [
        types.TextContent(
            type="text",
            text=json.dumps(
                {
                    "total_urls": len(urls),
                    "successful": sum(1 for r in results if r.get("success")),
                    "failed": sum(1 for r in results if not r.get("success")),
                    "results": results,
                },
                indent=2,
                ensure_ascii=False,
            ),
        )
    ]


async def _tool_extract_emails(args: dict) -> list[types.TextContent]:
    url = args["url"]
    html = await fetch_url(url)

    text = extract_text_content(html)
    text_emails = extract_emails_from_text(text)
    mailto_emails = extract_mailto_emails(html)
    all_emails = sorted(set(text_emails + mailto_emails))

    return [
        types.TextContent(
            type="text",
            text=json.dumps(
                {
                    "url": url,
                    "email_count": len(all_emails),
                    "emails": all_emails,
                },
                indent=2,
                ensure_ascii=False,
            ),
        )
    ]


async def _tool_extract_links(args: dict) -> list[types.TextContent]:
    url = args["url"]
    domain_filter = args.get("domain_filter")

    html = await fetch_url(url)

    if domain_filter:
        links = extract_links_filtered(html, url, domain_filter)
    else:
        links = extract_links(html, url)

    return [
        types.TextContent(
            type="text",
            text=json.dumps(
                {
                    "url": url,
                    "domain_filter": domain_filter,
                    "link_count": len(links),
                    "links": links,
                },
                indent=2,
                ensure_ascii=False,
            ),
        )
    ]


# ─── Main Entry Point ────────────────────────────────────────────────────────

async def main() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="web-scraper",
                server_version="1.0.0",
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
