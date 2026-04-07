# Job Agent -- Unified AI Job Search Pipeline

## What This Is

A single integrated job search system that:
1. **Discovers** jobs from LinkedIn, Greenhouse API, Lever API, SerpAPI
2. **Scores** them against your resume using OpenAI/Anthropic LLM
3. **Evaluates** top matches with deep A-F analysis via Claude API (archetype detection, CV matching, level strategy, comp research, interview prep)
4. **Generates** tailored ATS-optimized PDFs with keyword injection from each JD
5. **Auto-applies** to Greenhouse/Lever/Workday forms via Playwright
6. **Emails** a digest summary with top matches and applied jobs
7. **Exports** everything to a GitHub Pages dashboard

Runs automatically on GitHub Actions (Mon & Fri), with a web dashboard at `https://{user}.github.io/job-agent/`.

## Architecture

```
DISCOVER -> FILTER -> SCORE -> EVALUATE -> GENERATE PDF -> APPLY -> DIGEST -> EXPORT
   |                    |         |            |              |        |         |
LinkedIn         OpenAI/Claude  Claude API  Playwright     Playwright SMTP   data.json
Greenhouse API   quick 0-100    deep A-F    HTML->PDF      auto-fill  email  -> gh-pages
Lever API        scoring        blocks      tailored CV    + submit
SerpAPI                                     per-JD
                                 |
                              SQLite DB  <- single source of truth
```

## Key Files

| File | Purpose |
|------|---------|
| `src/pipeline.py` | Main pipeline orchestrator (all 8 steps) |
| `src/evaluation/evaluator.py` | A-F evaluation via Claude API |
| `src/evaluation/pdf_builder.py` | Tailored PDF generation |
| `src/evaluation/prompts.py` | Evaluation prompt assembly |
| `src/export.py` | Dashboard data + markdown export |
| `src/matching/` | LLM scoring, parsing, filtering |
| `src/discovery/` | LinkedIn, Greenhouse, Lever, SerpAPI sources |
| `src/application/` | Auto-apply (Greenhouse, Lever, Workday) |
| `src/notifications/` | Email digest + notifications |
| `cv.md` | Canonical CV (source of truth) |
| `config/profile.yml` | Candidate identity, targets, comp |
| `config/settings.yaml` | Pipeline config (thresholds, models) |
| `config/companies.yaml` | Company whitelist + ATS tokens |
| `dashboard/index.html` | GitHub Pages dashboard |
| `data/jobs.db` | SQLite database (single source of truth) |
| `templates/cv-template.html` | HTML template for PDF generation |

## CLI Commands

```bash
python -m src.cli run              # Full pipeline (all 8 steps)
python -m src.cli discover         # Discovery only
python -m src.cli score            # Score unscored jobs
python -m src.cli digest           # Send email digest
python -m src.cli export-dashboard # Export dashboard data
python -m src.cli status           # Show pipeline status
python -m src.cli companies        # List target companies
python -m src.cli apply            # Auto-apply to eligible jobs
```

## Data Model

SQLite is the single source of truth. Tables:
- `jobs` -- discovered job postings
- `match_scores` -- LLM scores + A-F evaluation JSON
- `applications` -- application status and history
- `artifacts` -- generated PDFs, reports, cover letters
- `story_bank` -- accumulated STAR+R interview stories
- `pipeline_runs` -- run history for dashboard charts
- `notifications` -- deduplication for emails

## Config

`config/settings.yaml`:
- `matching.min_score_auto_apply: 75` -- auto-apply threshold
- `matching.min_score_notify: 68` -- email notification threshold
- `evaluation.min_score_evaluate: 75` -- deep A-F evaluation threshold
- `evaluation.max_evaluations_per_run: 15` -- cost control
- `application.dry_run: true` -- set false for live submissions

## Interactive Modes (Claude Code)

Career-ops modes remain available for interactive use:
- `/career-ops {JD}` -- paste a JD for instant evaluation
- `/career-ops apply` -- live form-filling assistant
- `/career-ops contacto` -- LinkedIn outreach drafting
- `/career-ops deep` -- deep company research
- `/career-ops ofertas` -- compare multiple offers
- `/career-ops tracker` -- view application status

These read from the same SQLite database as the automated pipeline.
