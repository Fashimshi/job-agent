"""Prompt assembly for A-F evaluation via Claude API.

Reads candidate context from cv.md, profile.yml, _profile.md and assembles
the evaluation prompt that produces structured JSON output.
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _read_file(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def build_evaluation_prompt(
    job_title: str,
    company: str,
    job_description: str,
    resume_text: str,
) -> str:
    """Build the full A-F evaluation prompt for Claude API."""

    profile_yml = _read_file(PROJECT_ROOT / "config" / "profile.yml")
    profile_md = _read_file(PROJECT_ROOT / "modes" / "_profile.md")

    return f"""You are an expert career advisor evaluating a job offer for a candidate.
Produce a structured evaluation with blocks A-F and a global score.

## Candidate Resume
{resume_text[:4000]}

## Candidate Profile
{profile_yml[:2000]}

## Candidate Framing & Archetypes
{profile_md[:2000]}

## Job Offer
**Title:** {job_title}
**Company:** {company}
**Description:**
{job_description[:5000]}

## Instructions

Evaluate this job offer and return a JSON object with this exact structure:

{{
  "archetype": "one of: AI Platform / LLMOps | Agentic / Automation | Data Scientist | Applied Scientist | AI Solutions Architect | AI Forward Deployed | Other",
  "score_5": <float 1.0-5.0, overall match score>,
  "recommendation": "apply | consider | skip",
  "block_a": {{
    "tl_dr": "<1-sentence summary of the role>",
    "domain": "<platform/agentic/ML/NLP/enterprise/other>",
    "function": "<build/research/consult/manage/deploy>",
    "seniority": "<junior/mid/senior/staff/lead/principal>",
    "remote": "<full remote/hybrid/onsite/unknown>",
    "team_size": "<mentioned team size or unknown>"
  }},
  "block_b": {{
    "matches": [
      {{"requirement": "<JD requirement>", "cv_evidence": "<exact line from resume>", "strength": "strong|moderate|weak"}}
    ],
    "gaps": [
      {{"requirement": "<JD requirement>", "severity": "blocker|nice_to_have", "mitigation": "<how to address>"}}
    ],
    "match_percentage": <int 0-100>
  }},
  "block_c": {{
    "detected_level": "<level in JD>",
    "candidate_level": "<candidate's natural level>",
    "level_strategy": "<1-2 sentences on how to position>",
    "downlevel_plan": "<if downleveled, what to negotiate>"
  }},
  "block_d": {{
    "estimated_comp": "<salary range estimate>",
    "market_position": "<above/at/below market>",
    "comp_score": <int 1-5>,
    "notes": "<comp context>"
  }},
  "block_e": {{
    "personalized_summary": "<3-4 sentence professional summary rewritten for this JD, injecting keywords>",
    "keywords": ["<15-20 ATS keywords from JD>"],
    "top_cv_changes": ["<top 5 CV customizations for this role>"]
  }},
  "block_f": {{
    "star_stories": [
      {{
        "title": "<story title>",
        "requirement": "<JD requirement this addresses>",
        "situation": "<S>",
        "task": "<T>",
        "action": "<A>",
        "result": "<R with metrics>",
        "reflection": "<what was learned>"
      }}
    ],
    "case_study": "<which project to present and how>",
    "red_flag_questions": ["<potential tough questions and how to answer>"]
  }},
  "block_g": {{
    "why_this_role": "<2-3 sentences>",
    "why_this_company": "<2-3 sentences>",
    "relevant_experience": "<2-3 sentences with metrics>",
    "good_fit": "<2-3 sentences>"
  }}
}}

RULES:
- NEVER invent experience or metrics not in the resume
- Cite exact lines from the resume when matching
- Be direct and actionable, no fluff
- Score interpretation: 4.5+ strong match, 4.0-4.4 good, 3.5-3.9 decent, <3.5 recommend against
- Generate 4-6 STAR+R stories in block_f
- Keywords in block_e should be exact phrases from the JD for ATS optimization
- The personalized_summary should bridge the candidate's experience to this specific role

Return ONLY the JSON object, no markdown code blocks or other text."""


def build_report_markdown(
    job_title: str,
    company: str,
    evaluation: dict,
    report_num: str,
    date: str,
    job_url: str = "",
) -> str:
    """Generate a markdown report from structured evaluation JSON."""

    a = evaluation.get("block_a", {})
    b = evaluation.get("block_b", {})
    c = evaluation.get("block_c", {})
    d = evaluation.get("block_d", {})
    e = evaluation.get("block_e", {})
    f = evaluation.get("block_f", {})
    g = evaluation.get("block_g", {})

    # Block B matches table
    matches_table = "| Requirement | CV Evidence | Strength |\n|---|---|---|\n"
    for m in b.get("matches", []):
        matches_table += f"| {m.get('requirement', '')} | {m.get('cv_evidence', '')} | {m.get('strength', '')} |\n"

    # Block B gaps table
    gaps_table = "| Requirement | Severity | Mitigation |\n|---|---|---|\n"
    for gap in b.get("gaps", []):
        gaps_table += f"| {gap.get('requirement', '')} | {gap.get('severity', '')} | {gap.get('mitigation', '')} |\n"

    # Block E keywords
    keywords = ", ".join(e.get("keywords", []))

    # Block E CV changes
    cv_changes = "\n".join(f"- {ch}" for ch in e.get("top_cv_changes", []))

    # Block F STAR stories
    star_table = "| # | Requirement | Story | S | T | A | R | Reflection |\n|---|---|---|---|---|---|---|---|\n"
    for i, s in enumerate(f.get("star_stories", []), 1):
        star_table += (
            f"| {i} | {s.get('requirement', '')} | {s.get('title', '')} | "
            f"{s.get('situation', '')} | {s.get('task', '')} | "
            f"{s.get('action', '')} | {s.get('result', '')} | "
            f"{s.get('reflection', '')} |\n"
        )

    # Block F red flags
    red_flags = "\n".join(f"- {q}" for q in f.get("red_flag_questions", []))

    return f"""# Evaluation: {company} -- {job_title}

**Date:** {date}
**Archetype:** {evaluation.get('archetype', 'Unknown')}
**Score:** {evaluation.get('score_5', 'N/A')}/5
**URL:** {job_url}
**Recommendation:** {evaluation.get('recommendation', 'N/A')}

---

## A) Role Summary

| Field | Value |
|---|---|
| TL;DR | {a.get('tl_dr', '')} |
| Domain | {a.get('domain', '')} |
| Function | {a.get('function', '')} |
| Seniority | {a.get('seniority', '')} |
| Remote | {a.get('remote', '')} |
| Team Size | {a.get('team_size', '')} |

## B) CV Match ({b.get('match_percentage', 'N/A')}%)

### Matches
{matches_table}

### Gaps
{gaps_table}

## C) Level & Strategy

- **Detected level:** {c.get('detected_level', '')}
- **Candidate level:** {c.get('candidate_level', '')}
- **Strategy:** {c.get('level_strategy', '')}
- **Downlevel plan:** {c.get('downlevel_plan', '')}

## D) Comp & Market

- **Estimated comp:** {d.get('estimated_comp', '')}
- **Market position:** {d.get('market_position', '')}
- **Comp score:** {d.get('comp_score', 'N/A')}/5
- **Notes:** {d.get('notes', '')}

## E) CV Personalization

**Personalized Summary:**
{e.get('personalized_summary', '')}

**Top CV Changes:**
{cv_changes}

## F) Interview Plan

### STAR+R Stories
{star_table}

**Case Study:** {f.get('case_study', '')}

**Red Flag Questions:**
{red_flags}

## G) Draft Application Answers

- **Why this role?** {g.get('why_this_role', '')}
- **Why this company?** {g.get('why_this_company', '')}
- **Relevant experience:** {g.get('relevant_experience', '')}
- **Good fit:** {g.get('good_fit', '')}

---

## Keywords
{keywords}
"""
