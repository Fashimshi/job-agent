# Mode: digest -- Email Digest of Top Matches

Sends a summary email of the best job matches found. This mode runs independently
of discovery/scoring and can be triggered at any time.

## When to Use

- After running `auto-discover` to email results
- As a standalone check: "email me the best matches"
- On a schedule (GitHub Actions sends digest after discovery)

## Pipeline

### Option A: From Python Agent Database
```bash
python -m src.cli digest
```
- Queries `data/jobs.db` for top 20 jobs scoring >= 70
- Sends HTML email with stats, qualified jobs table, applied jobs table
- Uses SMTP credentials from `.env`

### Option B: From Career-Ops Tracker
Read `data/applications.md` and generate a digest of:
1. **New evaluations** since last digest (status = Evaluated)
2. **Top matches** (score >= 4.0/5)
3. **Status updates** (Applied, Interview, Responded)
4. **Action items** (high-scoring offers not yet applied to)

Format as a clean summary and either:
- Display in console (if no SMTP configured)
- Send via email (if SMTP configured in `.env`)

## Email Configuration

Required in `.env`:
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=mohammedfauzan44@gmail.com
SMTP_PASSWORD=<app-password>
NOTIFICATION_EMAIL=mohammedfauzan44@gmail.com
```

## Why This Is Separate

The old pipeline's biggest bug was that the digest was chained to discovery + apply
in a single 90-minute pipeline. When apply timed out, digest never sent.

Now digest runs as its own independent step. It can never be blocked by other operations.
