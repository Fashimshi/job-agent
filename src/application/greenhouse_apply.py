from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import Page, async_playwright

from src.application.base_applicant import BaseApplicant
from src.tracking.models import ApplicantInfo, ApplicationResult, Job

logger = logging.getLogger(__name__)


class GreenhouseApplicant(BaseApplicant):
    """Auto-fill and optionally submit Greenhouse application forms."""

    async def apply(
        self,
        job: Job,
        cover_letter: str,
        resume_path: Path,
        screenshot_dir: Path,
        dry_run: bool = True,
    ) -> ApplicationResult:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / f"greenhouse_{job.id}.png"

        if not job.apply_url:
            return ApplicationResult(
                success=False,
                job_id=job.id,
                error_message="No apply URL found",
            )

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 900},
                )
                page = await context.new_page()

                await page.goto(job.apply_url, wait_until="domcontentloaded", timeout=60000)
                # Wait for the application form to actually render
                try:
                    await page.wait_for_selector(
                        '#first_name, #application_form, form[action*="application"]',
                        timeout=15000,
                    )
                except Exception:
                    pass  # Form might use non-standard selectors
                await page.wait_for_timeout(2000)

                # Fill standard fields
                await self._fill_form(page, cover_letter, resume_path)

                # Take screenshot before submission
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
            logger.error(f"Greenhouse apply failed for {job.title} at {job.company}: {e}")
            return ApplicationResult(
                success=False,
                job_id=job.id,
                screenshot_path=str(screenshot_path) if screenshot_path.exists() else None,
                error_message=str(e),
            )

    async def _fill_form(self, page: Page, cover_letter: str, resume_path: Path) -> None:
        """Fill Greenhouse application form fields."""

        # First name
        first_name = page.locator("#first_name")
        if await first_name.count() > 0:
            await first_name.fill(self.info.first_name)

        # Last name
        last_name = page.locator("#last_name")
        if await last_name.count() > 0:
            await last_name.fill(self.info.last_name)

        # Email
        email = page.locator("#email")
        if await email.count() > 0:
            await email.fill(self.info.email)

        # Phone
        phone = page.locator("#phone")
        if await phone.count() > 0:
            await phone.fill(self.info.phone)

        # LinkedIn URL — try common selectors
        for selector in [
            'input[name*="linkedin" i]',
            'input[placeholder*="linkedin" i]',
            'input[id*="linkedin" i]',
        ]:
            linkedin = page.locator(selector)
            if await linkedin.count() > 0:
                await linkedin.first.fill(self.info.linkedin_url)
                break

        # Resume upload
        resume_input = page.locator('input[type="file"]').first
        if await resume_input.count() > 0:
            await resume_input.set_input_files(str(resume_path))
            logger.info("Resume uploaded")
            await page.wait_for_timeout(1000)

        # Cover letter — try textarea first, then file upload
        cover_textarea = page.locator('textarea[name*="cover_letter" i], textarea[id*="cover_letter" i]')
        if await cover_textarea.count() > 0:
            await cover_textarea.first.fill(cover_letter)
        else:
            # Some Greenhouse forms have a text input for cover letter
            cover_input = page.locator(
                'textarea[placeholder*="cover letter" i], '
                'textarea[name*="cover" i]'
            )
            if await cover_input.count() > 0:
                await cover_input.first.fill(cover_letter)

        # Location / Current Location
        for selector in [
            'input[name*="location" i]',
            'input[id*="location" i]',
        ]:
            loc = page.locator(selector)
            if await loc.count() > 0:
                await loc.first.fill(self.info.location)
                break

        # Work authorization — look for select dropdowns or radio buttons
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
                    await select.first.select_option(label="Yes")
                except Exception:
                    try:
                        await select.first.select_option(value="Yes")
                    except Exception:
                        pass

    async def _submit(self, page: Page) -> None:
        """Submit the application form."""
        # Try multiple selectors in order of specificity
        selectors = [
            '#submit_app',
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Submit Application")',
            'button:has-text("Submit")',
            'button:has-text("Apply")',
            'a:has-text("Submit")',
        ]
        for selector in selectors:
            btn = page.locator(selector).first
            if await btn.count() > 0:
                await btn.click()
                await page.wait_for_timeout(3000)
                return

        raise RuntimeError("Could not find submit button")
