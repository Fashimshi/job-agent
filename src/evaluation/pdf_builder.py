"""Build ATS-optimized PDF from evaluation data.

Reads cv.md + evaluation Block E (personalized summary, keywords),
fills the HTML template, and calls generate-pdf.mjs via subprocess.
"""
from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from src.tracking.models import Artifact, Job

if TYPE_CHECKING:
    from src.tracking.database import Database

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class PdfBuilder:
    """Generate ATS-optimized tailored PDFs from evaluation data."""

    def __init__(self, db: Database):
        self.db = db
        self.template_path = PROJECT_ROOT / "templates" / "cv-template.html"
        self.cv_path = PROJECT_ROOT / "cv.md"
        self.fonts_dir = PROJECT_ROOT / "fonts"
        self.output_dir = PROJECT_ROOT / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build(self, job: Job, evaluation: dict) -> str | None:
        """Generate a tailored PDF for a job. Returns output path or None."""
        block_e = evaluation.get("block_e", {})
        block_a = evaluation.get("block_a", {})

        personalized_summary = block_e.get("personalized_summary", "")
        keywords = block_e.get("keywords", [])
        cv_changes = block_e.get("top_cv_changes", [])

        if not personalized_summary:
            logger.warning(f"No personalized summary for {job.title} at {job.company}, skipping PDF")
            return None

        if not self.template_path.exists():
            logger.error(f"CV template not found: {self.template_path}")
            return None

        # Read CV and template
        cv_content = self.cv_path.read_text(encoding="utf-8") if self.cv_path.exists() else ""
        template = self.template_path.read_text(encoding="utf-8")

        # Read profile for contact info
        profile = self._read_profile()

        # Build competency tags from keywords
        competencies_html = "".join(
            f'<span class="competency-tag">{kw}</span>' for kw in keywords[:8]
        )

        # Build experience HTML from cv.md
        experience_html = self._cv_to_experience_html(cv_content)
        projects_html = self._cv_to_projects_html(cv_content)
        education_html = self._cv_to_education_html(cv_content)
        skills_html = self._cv_to_skills_html(cv_content)

        # Determine paper format
        location = (block_a.get("remote", "") + " " + (job.location or "")).lower()
        paper_format = "letter" if any(w in location for w in ["us", "usa", "united states", "america", "canada"]) else "a4"
        page_width = "8.5in" if paper_format == "letter" else "210mm"

        # Fill template placeholders
        html = template
        replacements = {
            "{{LANG}}": "en",
            "{{PAGE_WIDTH}}": page_width,
            "{{NAME}}": profile.get("full_name", "Fauzan Mohammed"),
            "{{EMAIL}}": profile.get("email", ""),
            "{{LINKEDIN_URL}}": f"https://{profile.get('linkedin', '')}",
            "{{LINKEDIN_DISPLAY}}": profile.get("linkedin", ""),
            "{{PORTFOLIO_URL}}": profile.get("portfolio_url", ""),
            "{{PORTFOLIO_DISPLAY}}": profile.get("portfolio_url", "").replace("https://", ""),
            "{{LOCATION}}": profile.get("location", ""),
            "{{SECTION_SUMMARY}}": "Professional Summary",
            "{{SUMMARY_TEXT}}": personalized_summary,
            "{{SECTION_COMPETENCIES}}": "Core Competencies",
            "{{COMPETENCIES}}": competencies_html,
            "{{SECTION_EXPERIENCE}}": "Work Experience",
            "{{EXPERIENCE}}": experience_html,
            "{{SECTION_PROJECTS}}": "Projects",
            "{{PROJECTS}}": projects_html,
            "{{SECTION_EDUCATION}}": "Education",
            "{{EDUCATION}}": education_html,
            "{{SECTION_CERTIFICATIONS}}": "Certifications",
            "{{CERTIFICATIONS}}": "",
            "{{SECTION_SKILLS}}": "Technical Skills",
            "{{SKILLS}}": skills_html,
        }

        for placeholder, value in replacements.items():
            html = html.replace(placeholder, value)

        # Write HTML to temp file and generate PDF
        company_slug = re.sub(r"[^a-z0-9]+", "-", job.company.lower()).strip("-")
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output_path = self.output_dir / f"cv-{company_slug}-{date}.pdf"

        try:
            with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False, encoding="utf-8") as f:
                f.write(html)
                html_path = f.name

            result = subprocess.run(
                ["node", str(PROJECT_ROOT / "generate-pdf.mjs"), html_path, str(output_path), f"--format={paper_format}"],
                capture_output=True, text=True, timeout=30, cwd=str(PROJECT_ROOT),
            )

            if result.returncode != 0:
                logger.error(f"PDF generation failed: {result.stderr}")
                return None

            # Store artifact
            artifact = Artifact(
                job_id=job.id,
                type="pdf",
                file_path=str(output_path.relative_to(PROJECT_ROOT)),
                created_at=datetime.now(timezone.utc),
                metadata_json=f'{{"format": "{paper_format}", "keywords_count": {len(keywords)}}}',
            )
            self.db.insert_artifact(artifact)

            logger.info(f"  PDF generated: {output_path.name}")
            return str(output_path)

        except subprocess.TimeoutExpired:
            logger.error("PDF generation timed out")
            return None
        except Exception as e:
            logger.error(f"PDF generation error: {e}")
            return None

    def _read_profile(self) -> dict:
        """Read candidate profile from config/profile.yml."""
        profile_path = PROJECT_ROOT / "config" / "profile.yml"
        if not profile_path.exists():
            return {}
        try:
            import yaml
            with open(profile_path) as f:
                data = yaml.safe_load(f)
            return data.get("candidate", {})
        except Exception:
            return {}

    def _cv_to_experience_html(self, cv: str) -> str:
        """Convert CV markdown experience section to HTML."""
        # Extract Work Experience section
        match = re.search(r"## Work Experience\s*\n(.*?)(?=\n## |\Z)", cv, re.DOTALL)
        if not match:
            return ""

        html = ""
        section = match.group(1)
        # Parse each job entry (### Title\n**Company** | Location | Date)
        jobs = re.split(r"### ", section)
        for job_text in jobs:
            if not job_text.strip():
                continue
            lines = job_text.strip().split("\n")
            title = lines[0].strip()
            company_line = lines[1].strip() if len(lines) > 1 else ""

            # Parse company/location/date from **Company** | Location | Date
            company_match = re.match(r"\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|\s*(.+)", company_line)
            company = company_match.group(1) if company_match else ""
            location = company_match.group(2) if company_match else ""
            period = company_match.group(3) if company_match else ""

            bullets = ""
            for line in lines[2:]:
                line = line.strip()
                if line.startswith("- "):
                    bullet_text = line[2:]
                    bullets += f"<li>{bullet_text}</li>\n"

            html += f"""<div class="job avoid-break">
  <div class="job-header">
    <span class="job-company">{company}</span>
    <span class="job-period">{period}</span>
  </div>
  <div class="job-role">{title}</div>
  <div class="job-location">{location}</div>
  <ul>{bullets}</ul>
</div>\n"""

        return html

    def _cv_to_projects_html(self, cv: str) -> str:
        match = re.search(r"## Projects\s*\n(.*?)(?=\n## |\Z)", cv, re.DOTALL)
        if not match:
            return ""
        html = ""
        section = match.group(1)
        projects = re.split(r"### ", section)
        for proj in projects:
            if not proj.strip():
                continue
            lines = proj.strip().split("\n")
            title = lines[0].strip()
            desc_lines = [l.strip()[2:] for l in lines[1:] if l.strip().startswith("- ")]
            desc = "<br>".join(desc_lines)
            html += f"""<div class="project">
  <span class="project-title">{title}</span>
  <div class="project-desc">{desc}</div>
</div>\n"""
        return html

    def _cv_to_education_html(self, cv: str) -> str:
        match = re.search(r"## Education\s*\n(.*?)(?=\n## |\Z)", cv, re.DOTALL)
        if not match:
            return ""
        html = ""
        section = match.group(1)
        entries = re.split(r"### ", section)
        for entry in entries:
            if not entry.strip():
                continue
            lines = entry.strip().split("\n")
            degree = lines[0].strip()
            school_line = lines[1].strip() if len(lines) > 1 else ""
            school_match = re.match(r"\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|\s*(.+)", school_line)
            school = school_match.group(1) if school_match else school_line
            location = school_match.group(2) if school_match else ""
            period = school_match.group(3) if school_match else ""
            html += f"""<div class="edu-item">
  <div class="edu-header">
    <span class="edu-title">{degree} -- <span class="edu-org">{school}</span></span>
    <span class="edu-year">{period}</span>
  </div>
  <div class="edu-desc">{location}</div>
</div>\n"""
        return html

    def _cv_to_skills_html(self, cv: str) -> str:
        match = re.search(r"## Technical Skills\s*\n(.*?)(?=\n## |\Z)", cv, re.DOTALL)
        if not match:
            return ""
        html = '<div class="skills-grid">'
        for line in match.group(1).strip().split("\n"):
            line = line.strip()
            if line.startswith("**") and ":**" in line:
                cat_match = re.match(r"\*\*(.+?):\*\*\s*(.*)", line)
                if cat_match:
                    cat = cat_match.group(1)
                    items = cat_match.group(2)
                    html += f'<span class="skill-item"><span class="skill-category">{cat}:</span> {items}</span> '
        html += "</div>"
        return html
