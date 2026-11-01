<!--
Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
LLM: nvidia nemotron ultra
Licensed under MIT License
-->

# How to Generate HTML Documentation with Mermaid Diagrams

## Overview

The architecture documentation is generated as a single self-contained HTML file (`ocs/architecture.html`) that includes:

- Mermaid.js diagrams rendered in-browser (no build step needed)
- Fullscreen modal for diagram viewing
- Responsive design
- All diagrams embedded in the HTML

## Prerequisites

- Mermaid.js loaded from CDN (no local install required)
- Modern browser with ES6 support

## File Structure

```
ocs/
├── architecture.html      # Main documentation (self-contained)
├── role-based-agent-architecture.md  # Source Markdown
└── HOWTO_HTML_DOCS.md     # This file
```

## Creating New Diagrams

### 1. Add Mermaid Code Block in HTML

Edit `ocs/architecture.html` and add a new diagram section:

```html
<section id="your-section">
    <h2>Your Section Title</h2>
    <div class="diagram-container">
        <div class="mermaid" id="your-diagram-id">
graph TD
    A[Start] --> B[Process]
    B --> C[End]
        </div>
    </div>
</section>
```

### 2. Mermaid Diagram Types Supported

| Type | Syntax | Use Case |
|------|--------|----------|
| Flowchart | `graph TD` / `flowchart LR` | Process flows, sequences |
| Sequence | `sequenceDiagram` | API calls, interactions |
| Class | `classDiagram` | Data structures |
| State | `stateDiagram-v2` | State machines |
| Git | `gitGraph` | Branching workflows |
| Gantt | `gantt` | Timelines |

### 3. Diagram Styling

The HTML includes CSS for:
- `.diagram-container` — white card with border
- `.mermaid` — centered content
- Fullscreen modal support

### 4. Adding to Fullscreen Modal

Diagrams are automatically included in the fullscreen modal via the JavaScript:

```javascript
const diagrams = document.querySelectorAll('.mermaid');
// Each diagram with an ID gets added to modal
```

Make sure each `.mermaid` div has a unique `id` attribute.

## Updating Documentation

1. Edit the Markdown source: `ocs/role-based-agent-architecture.md`
2. Update corresponding diagrams in `ocs/architecture.html`
3. Open `ocs/architecture.html` in browser to verify
4. Commit both files

## Viewing

```bash
# Open in browser (from project root)
xdg-open ocs/architecture.html
# or
open ocs/architecture.html  # macOS
```

## Features

- **No build step** — Mermaid renders client-side from CDN
- **Fullscreen** — Click "⛶ Fullscreen All Diagrams" button
- **Zoom/Pan** — Mouse wheel zoom, drag to pan in fullscreen
- **Responsive** — Works on mobile/tablet
- **Printable** — CSS includes print styles

## Adding New Sections

1. Add `<section id="...">` in HTML
2. Add entry to Table of Contents (`.toc` ul)
3. Include diagram(s) in `.diagram-container`
4. Update this HOWTO if new patterns emerge