# Web Scraper MCP Server

A lightweight, dependency-minimal web scraping server implementing the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). Uses **httpx** for HTTP requests and **regex** for HTML parsing — no BeautifulSoup, no lxml, no heavy dependencies.

> **$19/month** — Subscribe at [stripe.com](https://buy.stripe.com/dRm6oJ4Hd2Jugek0wz1oI0m)

## Tools

### 1. `scrape_url(url, selector?)`

Fetch a single URL and extract structured content:

- **Title** — content of the `<title>` tag
- **Meta description** — content of `<meta name="description">`
- **Text content** — all visible text with HTML tags stripped
- **Links** — all `<a href="...">` with absolute URLs and link text
- **Images** — all `<img src="...">` with absolute URLs and alt text

If a **CSS selector** is provided, returns matching elements with their text, attributes, nested links, and nested images instead.

**Supported selector patterns:**

| Pattern | Example | Matches |
|---------|---------|---------|
| Tag | `h1`, `div`, `p` | All elements with that tag |
| Class | `.content`, `.article` | Elements with the given class |
| ID | `#main`, `#header` | Element with the given ID |
| Attribute | `[href]`, `[data-type]` | Elements with the attribute |
| Attr = value | `[type="submit"]` | Elements with exact attribute value |
| Combined | `div.content#main` | All of the above together |

**Examples:**

```
# Get everything
scrape_url("https://example.com")

# Extract specific elements
scrape_url("https://example.com", "h1")
scrape_url("https://example.com", ".article-body")
scrape_url("https://example.com", "div.content a")
```

### 2. `scrape_urls(urls[])`

Batch scrape multiple URLs concurrently. Returns title, text snippet (first 500 chars), link count, and image count for each URL.

**Example:**

```
scrape_urls(["https://example.com", "https://example.org"])
```

### 3. `extract_emails(url)`

Extract all email addresses from a web page. Scans both visible text and `mailto:` links. Deduplicates results.

**Example:**

```
extract_emails("https://example.com/contact")
```

### 4. `extract_links(url, domain_filter?)`

Extract all hyperlinks from a page. Optionally filter by domain (substring match on hostname).

**Examples:**

```
# All links
extract_links("https://example.com")

# Only links pointing to a specific domain
extract_links("https://example.com", "docs.python.org")
```

## Installation

```bash
# Clone the repository
git clone https://github.com/nousresearch/web-scraper-mcp.git
cd web-scraper-mcp

# Install dependencies
pip install -r requirements.txt
```

## Usage

### As a standalone MCP server

```bash
python server.py
```

This starts a stdio-based MCP server. Connect it to any MCP client (Claude Desktop, VS Code extension, etc.).

### With Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "web-scraper": {
      "command": "python",
      "args": ["/path/to/web-scraper-mcp/server.py"]
    }
  }
}
```

### With VS Code (Cline / Roo Code)

Add to your MCP settings:

```json
{
  "mcpServers": {
    "web-scraper": {
      "command": "python",
      "args": ["/path/to/web-scraper-mcp/server.py"]
    }
  }
}
```

## Deployment

This server is compatible with [Smithery.ai](https://smithery.ai). Deploy with one click or via the provided `smithery.yaml`.

## Dependencies

Minimal and intentionally slim:

| Package | Version | Purpose |
|---------|---------|---------|
| `mcp` | >= 1.0.0 | Model Context Protocol SDK |
| `httpx` | >= 0.27.0 | Async HTTP client with connection pooling |

## Pricing

**$19/month** — [Subscribe on Stripe](https://buy.stripe.com/dRm6oJ4Hd2Jugek0wz1oI0m)

## License

MIT
