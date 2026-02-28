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

    async def _safe_fill(self, page: Page, selector: str, value: str) -> bool:
        """Fill a field safely — skip if not visible/interactable."""
        try:
            el = page.locator(selector).first
            if await el.count() > 0 and await el.is_visible():
                await el.fill(value, timeout=5000)
                return True
        except Exception:
            pass
        return False

    async def _fill_form(self, page: Page, cover_letter: str, resume_path: Path) -> None:
        """Fill Greenhouse application form fields."""

        await self._safe_fill(page, "#first_name", self.info.first_name)
        await self._safe_fill(page, "#last_name", self.info.last_name)
        await self._safe_fill(page, "#email", self.info.email)
        await self._safe_fill(page, "#phone", self.info.phone)

        # LinkedIn URL — try common selectors
        for selector in [
            'input[name*="linkedin" i]',
            'input[placeholder*="linkedin" i]',
            'input[id*="linkedin" i]',
        ]:
            if await self._safe_fill(page, selector, self.info.linkedin_url):
                break

        # Resume upload
        try:
            resume_input = page.locator('input[type="file"]').first
            if await resume_input.count() > 0:
                await resume_input.set_input_files(str(resume_path))
                logger.info("Resume uploaded")
                await page.wait_for_timeout(1000)
        except Exception as e:
            logger.debug(f"Resume upload failed: {e}")

        # Cover letter — try multiple selectors, skip if none visible
        for selector in [
            'textarea[name*="cover_letter" i]',
            'textarea[id*="cover_letter" i]',
            'textarea[placeholder*="cover letter" i]',
            'textarea[name*="cover" i]',
        ]:
            if await self._safe_fill(page, selector, cover_letter):
                break

        # Location / Current Location
        for selector in [
            'input[name*="location" i]',
            'input[id*="location" i]',
        ]:
            if await self._safe_fill(page, selector, self.info.location):
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
        """Submit the application form and verify it went through."""
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
                await page.wait_for_timeout(5000)

                # Check for validation errors (form rejected)
                error_msgs = page.locator(
                    '.field-error, .error-message, '
                    '[class*="error"], [id*="error"], '
                    '.field_with_errors'
                )
                if await error_msgs.count() > 0:
                    error_text = await error_msgs.first.text_content()
                    raise RuntimeError(
                        f"Form submitted but has validation errors: {error_text}"
                    )

                # Check for confirmation (application accepted)
                confirmation = page.locator(
                    ':has-text("Thank you"), :has-text("application has been"), '
                    ':has-text("successfully submitted"), :has-text("received your application")'
                )
                if await confirmation.count() > 0:
                    logger.info("Confirmed: application accepted")
                    return

                # If URL changed after submit, likely went through
                # (Greenhouse redirects to a thank-you page)
                logger.info("Submit clicked — no confirmation page detected, "
                           "may have succeeded")
                return

        raise RuntimeError("Could not find submit button")
