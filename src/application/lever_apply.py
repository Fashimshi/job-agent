from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import Page, async_playwright

from src.application.base_applicant import BaseApplicant
from src.tracking.models import ApplicantInfo, ApplicationResult, Job

logger = logging.getLogger(__name__)


class LeverApplicant(BaseApplicant):
    """Auto-fill and optionally submit Lever application forms."""

    async def apply(
        self,
        job: Job,
        cover_letter: str,
        resume_path: Path,
        screenshot_dir: Path,
        dry_run: bool = True,
    ) -> ApplicationResult:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / f"lever_{job.id}.png"

        apply_url = job.apply_url
        if not apply_url:
            # Lever apply URLs typically end with /apply
            apply_url = job.posting_url.rstrip("/") + "/apply"

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 900},
                )
                page = await context.new_page()

                await page.goto(apply_url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)

                await self._fill_form(page, cover_letter, resume_path)

                await page.screenshot(path=str(screenshot_path), full_page=True)
                logger.info(f"Screenshot saved: {screenshot_path}")

                if not dry_run:
                    await self._submit(page)
                    logger.info(f"Application submitted for {job.title} at {job.company}")
                else:
                    logger.info(f"DRY RUN: Form filled but NOT submitted for {job.title} at {job.company}")

                await browser.close()

            return ApplicationResult(
                success=True,
                job_id=job.id,
                screenshot_path=str(screenshot_path),
                submitted_at=datetime.now(timezone.utc) if not dry_run else None,
            )

        except Exception as e:
            logger.error(f"Lever apply failed for {job.title} at {job.company}: {e}")
            return ApplicationResult(
                success=False,
                job_id=job.id,
                screenshot_path=str(screenshot_path) if screenshot_path.exists() else None,
                error_message=str(e),
            )

    async def _fill_form(self, page: Page, cover_letter: str, resume_path: Path) -> None:
        """Fill Lever application form fields."""

        # Lever forms use name="name" for full name
        name_input = page.locator('input[name="name"]')
        if await name_input.count() > 0:
            await name_input.fill(f"{self.info.first_name} {self.info.last_name}")

        # Email
        email_input = page.locator('input[name="email"]')
        if await email_input.count() > 0:
            await email_input.fill(self.info.email)

        # Phone
        phone_input = page.locator('input[name="phone"]')
        if await phone_input.count() > 0:
            await phone_input.fill(self.info.phone)

        # Current company
        company_input = page.locator('input[name="org"]')
        if await company_input.count() > 0:
            await company_input.fill(self.info.current_company)

        # LinkedIn URL
        for selector in [
            'input[name="urls[LinkedIn]"]',
            'input[name*="linkedin" i]',
            'input[placeholder*="linkedin" i]',
        ]:
            linkedin = page.locator(selector)
            if await linkedin.count() > 0:
                await linkedin.first.fill(self.info.linkedin_url)
                break

        # Resume upload — Lever uses a file input or drag-and-drop
        resume_input = page.locator('input[type="file"][name="resume"]')
        if await resume_input.count() > 0:
            await resume_input.set_input_files(str(resume_path))
            logger.info("Resume uploaded")
            await page.wait_for_timeout(1000)
        else:
            # Try any file input
            file_input = page.locator('input[type="file"]').first
            if await file_input.count() > 0:
                await file_input.set_input_files(str(resume_path))
                await page.wait_for_timeout(1000)

        # Cover letter — Lever typically has a textarea
        cover_textarea = page.locator(
            'textarea[name="comments"], '
            'textarea[name*="cover" i], '
            'textarea[placeholder*="cover letter" i]'
        )
        if await cover_textarea.count() > 0:
            await cover_textarea.first.fill(cover_letter)
        else:
            # Fallback — any textarea that isn't for other fields
            textareas = page.locator("textarea")
            if await textareas.count() > 0:
                await textareas.first.fill(cover_letter)

        # Location
        location_input = page.locator('input[name*="location" i]')
        if await location_input.count() > 0:
            await location_input.first.fill(self.info.location)

        # GitHub URL (optional)
        if self.info.github_url:
            for selector in [
                'input[name="urls[GitHub]"]',
                'input[name*="github" i]',
                'input[placeholder*="github" i]',
            ]:
                github = page.locator(selector)
                if await github.count() > 0:
                    await github.first.fill(self.info.github_url)
                    break

        # Portfolio URL (optional)
        if self.info.portfolio_url:
            for selector in [
                'input[name="urls[Portfolio]"]',
                'input[name*="portfolio" i]',
                'input[placeholder*="portfolio" i]',
                'input[name*="website" i]',
            ]:
                portfolio = page.locator(selector)
                if await portfolio.count() > 0:
                    await portfolio.first.fill(self.info.portfolio_url)
                    break

        # Work authorization handling
        await self._handle_authorization(page)

    async def _handle_authorization(self, page: Page) -> None:
        """Handle work authorization questions."""
        # Look for work authorization select/dropdown
        for selector in [
            'select[name*="authorized" i]',
            'select[name*="authorization" i]',
            'select[name*="sponsorship" i]',
            'select[id*="authorized" i]',
        ]:
            select = page.locator(selector)
            if await select.count() > 0:
                try:
                    # Try selecting "Yes" for work authorization
                    if self.info.work_authorized:
                        await select.first.select_option(label="Yes")
                    else:
                        await select.first.select_option(label="No")
                except Exception:
                    try:
                        value = "Yes" if self.info.work_authorized else "No"
                        await select.first.select_option(value=value)
                    except Exception:
                        pass

        # Look for sponsorship requirement questions
        for selector in [
            'select[name*="sponsor" i]',
            'select[id*="sponsor" i]',
        ]:
            select = page.locator(selector)
            if await select.count() > 0:
                try:
                    value = "Yes" if self.info.sponsorship_needed else "No"
                    await select.first.select_option(label=value)
                except Exception:
                    try:
                        await select.first.select_option(value=value)
                    except Exception:
                        pass

    async def _submit(self, page: Page) -> None:
        """Submit the Lever application form and verify it went through."""
        pre_url = page.url

        submit_btn = page.locator(
            'button[type="submit"], '
            'button:has-text("Submit application"), '
            'button:has-text("Submit"), '
            'button:has-text("Apply")'
        ).first

        if await submit_btn.count() == 0:
            raise RuntimeError("Could not find submit button")

        await submit_btn.click()
        await page.wait_for_timeout(5000)

        # 1. URL change — Lever redirects to a thank-you page on success
        if page.url != pre_url:
            logger.info("Confirmed: URL changed after submit (redirected)")
            return

        # 2. Confirmation text on the page
        page_text = (await page.text_content("body") or "").lower()
        confirmation_phrases = [
            "thank you", "application has been",
            "successfully submitted", "received your application",
            "thanks for applying", "application received",
            "thanks for your interest",
        ]
        if any(phrase in page_text for phrase in confirmation_phrases):
            logger.info("Confirmed: application accepted (confirmation text found)")
            return

        # 3. Visible validation errors — Lever uses .application-error, .error classes
        error_msgs = page.locator(
            '.application-error:visible, '
            '.error:visible, '
            '.error-message:visible, '
            '[class*="error"]:visible:not(script):not(style)'
        )
        if await error_msgs.count() > 0:
            error_texts = []
            for i in range(min(await error_msgs.count(), 3)):
                txt = await error_msgs.nth(i).text_content()
                if txt and txt.strip():
                    error_texts.append(txt.strip())
            if error_texts:
                raise RuntimeError(
                    f"Form has validation errors: {'; '.join(error_texts)}"
                )

        # No confirmation and no errors — cannot confirm submission
        raise RuntimeError(
            "Submit clicked but no confirmation detected — "
            "application may not have gone through"
        )
