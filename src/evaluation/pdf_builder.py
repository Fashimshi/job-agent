"""Build ATS-optimized PDF from evaluation data using LaTeX.

Reads cv.md + evaluation Block E (personalized summary, keywords),
fills the LaTeX template, and compiles with pdflatex.
Matches Fauzan's existing resume style: Computer Modern font,
small-caps section headers, horizontal rules, bold key phrases.
"""
from __future__ import annotations

import logging
import re
import shutil
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


def _tex_escape(text: str) -> str:
    """Escape special LaTeX characters."""
    if not text:
        return ""
    replacements = [
        ("\\", r"\textbackslash{}"),
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _bold_phrases(text: str, keywords: list[str]) -> str:
    """Bold key phrases in bullet points (matching the resume style)."""
    for kw in keywords:
        if not kw or len(kw) < 3:
            continue
        pattern = re.compile(re.escape(kw), re.IGNORECASE)
        text = pattern.sub(lambda m: r"\textbf{" + m.group(0) + "}", text)
    return text


class PdfBuilder:
    """Generate ATS-optimized tailored PDFs using LaTeX."""

    def __init__(self, db: Database):
        self.db = db
        self.template_path = PROJECT_ROOT / "templates" / "cv-template.tex"
        self.cv_path = PROJECT_ROOT / "cv.md"
        self.output_dir = PROJECT_ROOT / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build(self, job: Job, evaluation: dict) -> str | None:
        """Generate a tailored PDF for a job. Returns output path or None."""
        block_e = evaluation.get("block_e", {})

        personalized_summary = block_e.get("personalized_summary", "")
        keywords = block_e.get("keywords", [])

        if not self.template_path.exists():
            logger.error(f"LaTeX template not found: {self.template_path}")
            return None

        cv_content = self.cv_path.read_text(encoding="utf-8") if self.cv_path.exists() else ""
        template = self.template_path.read_text(encoding="utf-8")
        profile = self._read_profile()

        # Build LaTeX sections from cv.md
        education_tex = self._build_education(cv_content)
        experience_tex = self._build_experience(cv_content, keywords)
        projects_tex = self._build_projects(cv_content, keywords)
        leadership_tex = self._build_leadership(cv_content)
        skills_tex = self._build_skills(cv_content, keywords)

        # Summary section (only if personalized)
        summary_section = ""
        if personalized_summary:
            escaped_summary = _tex_escape(personalized_summary)
            escaped_summary = _bold_phrases(escaped_summary, [_tex_escape(k) for k in keywords[:8]])
            summary_section = f"\\section{{Summary}}\n{escaped_summary}\n"

        projects_section = f"\\section{{Projects}}\n{projects_tex}\n" if projects_tex else ""
        leadership_section = f"\\section{{Leadership}}\n{leadership_tex}\n" if leadership_tex else ""

        # Fill template
        tex = template
        tex = tex.replace("<<NAME>>", _tex_escape(profile.get("full_name", "Fauzan Mohammed")))
        tex = tex.replace("<<EMAIL>>", _tex_escape(profile.get("email", "mohammedfauzan44@gmail.com")))
        tex = tex.replace("<<LINKEDIN>>", _tex_escape(profile.get("linkedin", "linkedin.com/in/fauzanmohammed")))
        tex = tex.replace("<<SUMMARY_SECTION>>", summary_section)
        tex = tex.replace("<<EDUCATION>>", education_tex)
        tex = tex.replace("<<EXPERIENCE>>", experience_tex)
        tex = tex.replace("<<PROJECTS_SECTION>>", projects_section)
        tex = tex.replace("<<LEADERSHIP_SECTION>>", leadership_section)
        tex = tex.replace("<<SKILLS>>", skills_tex)

        # Compile LaTeX to PDF
        company_slug = re.sub(r"[^a-z0-9]+", "-", job.company.lower()).strip("-")
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output_path = self.output_dir / f"cv-{company_slug}-{date}.pdf"

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tex_path = Path(tmpdir) / "cv.tex"
                tex_path.write_text(tex, encoding="utf-8")

                for _ in range(2):
                    result = subprocess.run(
                        ["pdflatex", "-interaction=nonstopmode",
                         "-output-directory", tmpdir, str(tex_path)],
                        capture_output=True, text=True, timeout=30, cwd=tmpdir,
                    )
                    if result.returncode != 0:
                        errors = [l for l in result.stdout.split("\n")
                                  if l.startswith("!") or "Error" in l]
                        logger.warning(f"pdflatex warnings: {'; '.join(errors[:3])}")

                pdf_output = Path(tmpdir) / "cv.pdf"
                if pdf_output.exists():
                    shutil.copy2(pdf_output, output_path)
                else:
                    logger.error(f"PDF not generated for {job.title} at {job.company}")
                    return None

            artifact = Artifact(
                job_id=job.id,
                type="pdf",
                file_path=str(output_path.relative_to(PROJECT_ROOT)),
                created_at=datetime.now(timezone.utc),
                metadata_json=f'{{"format": "latex", "keywords_count": {len(keywords)}}}',
            )
            self.db.insert_artifact(artifact)

            logger.info(f"  PDF generated: {output_path.name}")
            return str(output_path)

        except subprocess.TimeoutExpired:
            logger.error("pdflatex timed out")
            return None
        except FileNotFoundError:
            logger.error("pdflatex not found -- install texlive")
            return None
        except Exception as e:
            logger.error(f"PDF generation error: {e}")
            return None

    def _read_profile(self) -> dict:
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

    def _build_education(self, cv: str) -> str:
        match = re.search(r"## Education\s*\n(.*?)(?=\n## |\Z)", cv, re.DOTALL)
        if not match:
            return ""

        tex = ""
        entries = re.split(r"### ", match.group(1))
        for entry in entries:
            if not entry.strip():
                continue
            lines = entry.strip().split("\n")
            degree = lines[0].strip()
            school_line = lines[1].strip() if len(lines) > 1 else ""

            m = re.match(r"\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|\s*(.+)", school_line)
            if m:
                school, location, period = m.group(1), m.group(2), m.group(3)
            else:
                school, location, period = school_line.replace("**", ""), "", ""

            tex += (f"\\resumeHeading{{{_tex_escape(school)}}}{{{_tex_escape(location)}}}"
                    f"{{{_tex_escape(degree)}}}{{{_tex_escape(period)}}}\n")
        return tex

    def _build_experience(self, cv: str, keywords: list[str]) -> str:
        match = re.search(r"## Work Experience\s*\n(.*?)(?=\n## |\Z)", cv, re.DOTALL)
        if not match:
            return ""

        tex = ""
        escaped_kw = [_tex_escape(k) for k in keywords[:15]]
        entries = re.split(r"### ", match.group(1))

        for entry in entries:
            if not entry.strip():
                continue
            lines = entry.strip().split("\n")
            title = lines[0].strip()
            company_line = lines[1].strip() if len(lines) > 1 else ""

            m = re.match(r"\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|\s*(.+)", company_line)
            if m:
                company, location, period = m.group(1), m.group(2), m.group(3)
            else:
                company, location, period = company_line.replace("**", ""), "", ""

            tex += (f"\\resumeSubheading{{{_tex_escape(title)}}}{{{_tex_escape(period)}}}"
                    f"{{{_tex_escape(company)}}}{{{_tex_escape(location)}}}\n")
            tex += "\\begin{itemize}\n"

            for line in lines[2:]:
                line = line.strip()
                if line.startswith("- "):
                    bullet = _tex_escape(line[2:])
                    bullet = _bold_phrases(bullet, escaped_kw)
                    tex += f"  \\item {bullet}\n"

            tex += "\\end{itemize}\n\n"
        return tex

    def _build_projects(self, cv: str, keywords: list[str]) -> str:
        match = re.search(r"## Projects\s*\n(.*?)(?=\n## |\Z)", cv, re.DOTALL)
        if not match:
            return ""

        tex = ""
        escaped_kw = [_tex_escape(k) for k in keywords[:15]]
        entries = re.split(r"### ", match.group(1))

        for entry in entries:
            if not entry.strip():
                continue
            lines = entry.strip().split("\n")
            title = lines[0].strip()
            date = ""
            bullet_start = 1
            if len(lines) > 1 and lines[1].strip().startswith("*"):
                date = lines[1].strip().strip("*")
                bullet_start = 2

            tex += f"\\resumeProjectHeading{{{_tex_escape(title)}}}{{{_tex_escape(date)}}}\n"
            tex += "\\begin{itemize}\n"
            for line in lines[bullet_start:]:
                line = line.strip()
                if line.startswith("- "):
                    bullet = _tex_escape(line[2:])
                    bullet = _bold_phrases(bullet, escaped_kw)
                    tex += f"  \\item {bullet}\n"
            tex += "\\end{itemize}\n\n"
        return tex

    def _build_leadership(self, cv: str) -> str:
        match = re.search(r"## Leadership\s*\n(.*?)(?=\n## |\Z)", cv, re.DOTALL)
        if not match:
            return ""

        tex = ""
        entries = re.split(r"### ", match.group(1))
        for entry in entries:
            if not entry.strip():
                continue
            lines = entry.strip().split("\n")
            title = lines[0].strip()
            org_line = lines[1].strip() if len(lines) > 1 else ""

            m = re.match(r"\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|\s*(.+)", org_line)
            if m:
                org, location, period = m.group(1), m.group(2), m.group(3)
            else:
                org, location, period = org_line.replace("**", ""), "", ""

            tex += (f"\\resumeSubheading{{{_tex_escape(title)}}}{{{_tex_escape(period)}}}"
                    f"{{{_tex_escape(org)}}}{{{_tex_escape(location)}}}\n")
            tex += "\\begin{itemize}\n"
            for line in lines[2:]:
                line = line.strip()
                if line.startswith("- "):
                    tex += f"  \\item {_tex_escape(line[2:])}\n"
            tex += "\\end{itemize}\n\n"
        return tex

    def _build_skills(self, cv: str, keywords: list[str]) -> str:
        match = re.search(r"## Technical Skills\s*\n(.*?)(?=\n## |\Z)", cv, re.DOTALL)
        if not match:
            return ""

        tex = ""
        escaped_kw = [_tex_escape(k) for k in keywords[:15]]
        for line in match.group(1).strip().split("\n"):
            line = line.strip()
            if line.startswith("**") and ":**" in line:
                m = re.match(r"\*\*(.+?):\*\*\s*(.*)", line)
                if m:
                    cat = m.group(1)
                    items = _tex_escape(m.group(2))
                    items = _bold_phrases(items, escaped_kw)
                    tex += f"\\textbf{{{_tex_escape(cat)}:}} {items} \\\\\n"
        return tex
