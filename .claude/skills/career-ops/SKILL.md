---
name: career-ops
description: AI job search command center -- evaluate offers, generate CVs, scan portals, track applications
user_invocable: true
args: mode
---

# career-ops -- Router

## Mode Routing

Determine the mode from `{{mode}}`:

| Input | Mode |
|-------|------|
| (empty / no args) | `discovery` -- Show command menu |
| JD text or URL (no sub-command) | **`auto-pipeline`** |
| `oferta` | `oferta` |
| `ofertas` | `ofertas` |
| `contacto` | `contacto` |
| `deep` | `deep` |
| `pdf` | `pdf` |
| `training` | `training` |
| `project` | `project` |
| `tracker` | `tracker` |
| `pipeline` | `pipeline` |
| `apply` | `apply` |
| `scan` | `scan` |
| `batch` | `batch` |
| `auto-discover` | `auto-discover` |
| `digest` | `digest` |

**Auto-pipeline detection:** If `{{mode}}` is not a known sub-command AND contains JD text (keywords: "responsibilities", "requirements", "qualifications", "about the role", "we're looking for", company name + role) or a URL to a JD, execute `auto-pipeline`.

If `{{mode}}` is not a sub-command AND doesn't look like a JD, show discovery.

---

## Discovery Mode (no arguments)

Show this menu:

```
career-ops -- Command Center (Fauzan Mohammed)

Available commands:
  /career-ops {JD}           -> AUTO-PIPELINE: evaluate + report + PDF + tracker (paste text or URL)
  /career-ops pipeline       -> Process pending URLs from inbox (data/pipeline.md)
  /career-ops oferta         -> Evaluation only A-F (no auto PDF)
  /career-ops ofertas        -> Compare and rank multiple offers
  /career-ops contacto       -> LinkedIn power move: find contacts + draft message
  /career-ops deep           -> Deep research prompt about company
  /career-ops pdf            -> PDF only, ATS-optimized CV
  /career-ops training       -> Evaluate course/cert against North Star
  /career-ops project        -> Evaluate portfolio project idea
  /career-ops tracker        -> Application status overview
  /career-ops apply          -> Live application assistant (reads form + generates answers)
  /career-ops scan           -> Scan portals and discover new offers (interactive)
  /career-ops batch          -> Batch processing with parallel workers
  /career-ops auto-discover  -> Run automated Python discovery + LLM scoring
  /career-ops digest         -> Send email digest of top matches

Inbox: add URLs to data/pipeline.md -> /career-ops pipeline
Or paste a JD directly to run the full pipeline.
```

---

## Context Loading by Mode

After determining the mode, load the necessary files before executing:

### Modes that require `_shared.md` + their mode file:
Read `modes/_shared.md` + `modes/{mode}.md`

Applies to: `auto-pipeline`, `oferta`, `ofertas`, `pdf`, `contacto`, `apply`, `pipeline`, `scan`, `batch`

### Standalone modes (only their mode file):
Read `modes/{mode}.md`

Applies to: `tracker`, `deep`, `training`, `project`, `auto-discover`, `digest`

### Modes delegated to subagent:
For `scan`, `apply` (with Playwright), and `pipeline` (3+ URLs): launch as Agent with the content of `_shared.md` + `modes/{mode}.md` injected into the subagent prompt.

Execute the instructions from the loaded mode file.
