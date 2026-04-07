# Mode: auto-discover -- Automated Job Discovery + LLM Scoring

This mode integrates the automated discovery pipeline from the original job-agent.
It runs the Python discovery scripts to find jobs, then feeds them into the career-ops evaluation pipeline.

## Overview

Unlike the interactive `scan` mode (which uses Playwright to browse portals), this mode runs
the Python-based automated discovery that scrapes LinkedIn, Greenhouse APIs, Lever APIs, and
SerpAPI in parallel, then uses LLM scoring to rank matches.

## When to Use

- Scheduled automated runs (GitHub Actions, cron)
- When the user says "run the automated search" or "find new jobs"
- When bulk discovery is needed (not interactive browsing)

## Pipeline (composable steps -- each runs independently)

### Step 1: Discover
```bash
python -m src.cli discover
```
- Queries LinkedIn public API (100 results/query, 8 queries)
- Queries Greenhouse/Lever APIs for whitelisted companies
- Queries SerpAPI as fallback
- Deduplicates across sources
- Stores results in SQLite `data/jobs.db`

### Step 2: Score
```bash
python -m src.cli score
```
- Filters by keyword, seniority, location
- LLM scores each job against resume (OpenAI primary, Anthropic fallback)
- Parallel scoring (up to 10 concurrent)
- Stores scores in `data/jobs.db`

### Step 3: Digest Email
```bash
python -m src.cli digest
```
- Queries top 20 jobs scoring >= 70
- Sends HTML digest email via SMTP
- Runs independently -- never blocked by apply/notify steps
- Silent skip if no SMTP credentials configured

### Step 4: Add to Pipeline (NEW -- bridge to career-ops)
After discovery + scoring, top matches are added to `data/pipeline.md` for
career-ops evaluation (A-F blocks, PDF generation, tracker registration).

```
For each job with score >= 75:
  - Add to data/pipeline.md: `- [ ] {url} | {company} | {title} (auto-score: {score})`
```

Then run `/career-ops pipeline` to evaluate them with the full A-F framework.

## Key Differences from Old Pipeline

| Old (broken) | New (composable) |
|-------------|-----------------|
| Monolithic 90-min pipeline | Each step runs independently |
| Digest blocked by apply/notify | Digest runs as its own step |
| Auto-apply could timeout entire run | Auto-apply removed (career-ops uses human-in-the-loop) |
| All-or-nothing execution | Each step succeeds/fails independently |

## Configuration

Uses `config/settings.yaml` for:
- Discovery queries and filters
- Scoring thresholds and LLM models
- SMTP settings for digest email

Uses `config/companies.yaml` for:
- Company whitelist with ATS types
- Greenhouse/Lever API tokens

## Scheduling

For automated runs, use GitHub Actions or cron:
```yaml
# .github/workflows/discover.yml
on:
  schedule:
    - cron: "30 21 * * 1,5"  # Mon & Fri at 4:30 PM ET
```

Each step is a separate job with its own timeout:
- discover: 20 min
- score: 30 min
- digest: 5 min
