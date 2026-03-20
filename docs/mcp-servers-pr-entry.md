# MCP Servers Directory — PR Entry for Sentinel Execution MCP

This file contains the exact content needed to submit a pull request adding
Sentinel Execution MCP to the official MCP servers directory at
https://github.com/modelcontextprotocol/servers.

---

## 1. Entry Formats

### Table Row (use if the README section uses a Markdown table)

```markdown
| [Sentinel Execution MCP](https://github.com/rohith1125/sentinel-execution-mcp) | AI-controlled trading engine — watchlist, regime detection, risk/kill-switch, paper execution, governance, and audit tools for algorithmic trading | Finance / Trading |
```

### List Item (use if the README section uses a Markdown list)

```markdown
- [Sentinel Execution MCP](https://github.com/rohith1125/sentinel-execution-mcp) — AI-controlled trading engine — watchlist, regime detection, risk/kill-switch, paper execution, governance, and audit tools for algorithmic trading
```

---

## 2. Where to Place the Entry

In the `modelcontextprotocol/servers` README, locate the section for community
or third-party servers. Inside that section, find (or create) a
**Finance / Trading** subsection and insert the entry there. If the file uses
a flat alphabetical list, insert under **S** (Sentinel…).

---

## 3. Step-by-Step PR Submission Instructions

### Step 1 — Fork the repository

1. Open https://github.com/modelcontextprotocol/servers in your browser.
2. Click **Fork** (top-right corner).
3. Accept the default settings and click **Create fork**.

### Step 2 — Clone your fork locally

```bash
git clone https://github.com/<YOUR_GITHUB_USERNAME>/servers.git
cd servers
```

### Step 3 — Create a feature branch

```bash
git checkout -b add-sentinel-execution-mcp
```

### Step 4 — Edit the README

Open `README.md` (or whichever file lists community servers — check for
`docs/servers.md` or `README.md` in the repo root).

Find the appropriate section (Finance / Trading, or community servers), then
insert **one** of the two entry formats from Section 1 above.

If a Finance / Trading subsection does not yet exist, add it:

```markdown
### Finance / Trading

- [Sentinel Execution MCP](https://github.com/rohith1125/sentinel-execution-mcp) — AI-controlled trading engine — watchlist, regime detection, risk/kill-switch, paper execution, governance, and audit tools for algorithmic trading
```

### Step 5 — Commit the change

```bash
git add README.md
git commit -m "feat: add Sentinel Execution MCP (finance/trading)"
```

### Step 6 — Push your branch

```bash
git push -u origin add-sentinel-execution-mcp
```

### Step 7 — Open the Pull Request

1. Go to https://github.com/modelcontextprotocol/servers.
2. GitHub will show a banner: **"Compare & pull request"** — click it.
3. Fill in the PR form using the title and body below.

---

## 4. Suggested PR Title

```
feat: add Sentinel Execution MCP — AI trading engine (finance/trading)
```

## 5. Suggested PR Body

```markdown
## Summary

Adds **Sentinel Execution MCP** to the community servers list under the
Finance / Trading category.

### What is Sentinel Execution MCP?

Sentinel Execution MCP is an AI-controlled algorithmic trading control plane
exposed as a Model Context Protocol server. It gives LLM agents structured
tools for:

- **Watchlist management** — add, remove, and query tracked symbols
- **Market regime detection** — classify current market conditions (trending,
  ranging, volatile, etc.)
- **Risk management & kill-switch** — enforce position limits and halt trading
  programmatically
- **Paper execution** — simulate order fills without real capital at risk
- **Governance & audit** — log every agent decision for compliance review

### Links

- Repository: https://github.com/rohith1125/sentinel-execution-mcp
- MCP package: `@sentinel/mcp` v0.1.0 (TypeScript, `@modelcontextprotocol/sdk`)
- Engine: `sentinel-engine` v0.1.0 (Python ≥ 3.12, FastAPI + SQLAlchemy)

### Checklist

- [x] Entry follows the existing list/table format in the README
- [x] Link points directly to the public GitHub repository
- [x] Description is concise and accurate
- [x] Category is appropriate (Finance / Trading)
```
