# Design Spec: Inline Comments, Pydocs & Documentation Webpage
**Date:** 2026-05-07
**Project:** Email Alert Triage Agent

---

## Overview

Two deliverables:

1. **Inline comments and pydocs** — add comprehensive docstrings and inline comments to all 11 Python source files.
2. **Documentation webpage** — a standalone `docs/index.html` developer reference site.

---

## Part 1: Inline Comments & Pydocs

### Scope

All 11 Python source files in `src/`:

| File | Key additions |
|---|---|
| `agent.py` | Module docstring, function docstrings with Args/Returns, inline comments on pipeline flow |
| `config.py` | Class docstring, attribute docstrings, method docstrings with Returns |
| `database.py` | Function docstrings with Args/Returns, inline comments explaining WAL mode, thread-local pattern, write lock |
| `claude_client.py` | Function docstrings with Args/Returns/Raises, inline comments on cache key logic and injection scan |
| `classifier.py` | Module docstring, function docstrings, inline comment explaining confidence clamping and CASE_TYPES validation |
| `extractor.py` | Function docstrings with Args/Returns, inline comments on normalization rules in `generate_grouping_key` |
| `case_manager.py` | Function docstrings with Args/Returns, inline comments on each pipeline step, comment explaining no-auto-close rule |
| `email_reader.py` | Function docstrings with Args/Returns, inline comments on multipart walk and RFC 2047 decode |
| `email_sender.py` | Function docstrings with Args/Returns, inline comments explaining why `confirm=True` is correct in DEMO_MODE |
| `followup.py` | Function docstrings with Args/Returns, inline comment on `_ESCALATION_THRESHOLD` |
| `web/app.py` | Route docstrings, inline comment on `use_reloader=False` reason |

### Docstring style

Google-style docstrings throughout:

```python
def example(arg: str, flag: bool = True) -> dict:
    """One-line summary.

    Longer explanation of behaviour if needed — only when the summary
    line is not enough to understand the function.

    Args:
        arg: What this argument is.
        flag: What this flag controls and its effect.

    Returns:
        Dict with keys: 'result' (str), 'status' (bool).

    Raises:
        ValueError: If arg is empty.
        RuntimeError: If the external call fails.
    """
```

### Inline comment rules

- **Add** comments that explain the WHY: a hidden constraint, a subtle invariant, a non-obvious decision.
- **Do not** add comments that restate what the code already says clearly.
- **Target locations**: WAL pragma reason, write lock rationale, `confirm=True` in sender, no-auto-close enforcement, `quick_filter` purpose, grouping key normalization logic, injection detection layers, `use_reloader=False`.

---

## Part 2: Documentation Webpage

### Deliverable

`docs/index.html` — single standalone file, no build step, no external dependencies except a Google Fonts import and inline CSS/JS.

### Layout

Fixed left sidebar (220px) + scrolling main content. Sidebar stays fixed while content scrolls.

### Visual style

Full dark theme (GitHub dark palette):
- Background: `#0d1117`
- Sidebar: `#161b22`
- Borders: `#30363d`
- Primary text: `#f0f6fc`
- Secondary text: `#8b949e`
- Accent (links, active): `#58a6ff`
- Code: `#79c0ff` (types), `#ffa657` (params), `#ff7b72` (keywords), `#a5d6ff` (strings), `#56d364` (returns)

### Sidebar sections

```
EMAIL TRIAGE AGENT
Developer Reference

GETTING STARTED
  Overview
  Architecture
  Data Flow
  Quick Start

MODULES
  agent.py
  config.py
  database.py
  claude_client.py
  classifier.py
  extractor.py
  case_manager.py
  email_reader.py
  email_sender.py
  followup.py
  web/app.py

REFERENCE
  Database Schema
  Security Model
  Demo vs Production
```

Clicking any sidebar link smooth-scrolls to the corresponding `<section id="...">` anchor. Active section is highlighted in sidebar as user scrolls (IntersectionObserver).

### Main content sections

**Overview**
- One-paragraph description of what the agent does
- Stats row: 11 modules / 6 case types / 7 DB tables / Claude Haiku
- Quick-start commands in a code block

**Architecture**
- ASCII/styled data flow diagram showing module relationships
- Pipeline steps numbered 1–7

**Data Flow**
- Step-by-step narrative of an email's journey
- Colour-coded step badges (blue = input, green = AI, purple = persistence, red = output)

**Quick Start**
- Install, configure `.env`, run demo, open web UI — four code blocks

**Module Reference (one `<section>` per file)**

Each module section contains:
- Module filename as heading
- One-sentence purpose description
- For each `class`: name, docstring, attribute table
- For each `def`/method: a function card with:
  - Signature line with colour-coded types
  - Description paragraph
  - Args table (name, type, description)
  - Returns line
  - Raises list (if any)

**Database Schema**
- Table-by-table breakdown with column names, types, and purpose
- Index list with rationale

**Security Model**
- Prompt injection prevention steps
- Demo recipient enforcement
- No-auto-close rule

**Demo vs Production**
- Side-by-side comparison table

### Interactivity

- **Smooth scroll** on sidebar link click
- **Active section highlight** in sidebar via IntersectionObserver
- **Copy button** on every code block (copies to clipboard, shows "Copied!" for 1.5s)
- **Collapsible module sections** — click a module heading to collapse/expand its function cards (default: expanded)
- No external JS frameworks — vanilla JS only

### No-build constraint

Single file. All CSS inline in `<style>`. All JS inline in `<script>`. One optional Google Fonts CDN import for monospace font. Works by opening the file directly (`file://`) in any browser.

---

## Out of Scope

- Search functionality
- Auto-generated docs from source (no Sphinx, pdoc, mkdocs)
- Multiple HTML files / build pipeline
- Auth or hosting

---

## Success Criteria

- [ ] All 11 Python files have Google-style docstrings on every `class` and `def`
- [ ] Inline comments added at the 8 target locations listed above
- [ ] `docs/index.html` opens in browser with no errors
- [ ] All sidebar links scroll to the correct section
- [ ] Active section highlights correctly while scrolling
- [ ] Copy buttons work on all code blocks
- [ ] Module sections collapse/expand correctly
- [ ] Page works at `file://` (no server required)
