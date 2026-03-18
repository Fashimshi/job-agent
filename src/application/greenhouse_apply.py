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

        # Handle common custom required fields (dropdowns & text)
        await self._handle_custom_fields(page)

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

    async def _handle_custom_fields(self, page: Page) -> None:
        """Handle common custom Greenhouse fields that cause validation errors."""

        # Fill all visible required select dropdowns that are still on default
        selects = page.locator("select:visible")
        count = await selects.count()
        for i in range(count):
            select = selects.nth(i)
            try:
                # Skip if already has a value selected (not the blank default)
                current = await select.input_value(timeout=2000)
                if current:
                    continue

                # Get available options
                options = select.locator("option")
                opt_count = await options.count()
                if opt_count <= 1:
                    continue

                # Try to pick a sensible option based on the field label
                label_el = page.locator(f'label[for="{await select.get_attribute("id")}"]')
                label_text = ""
                if await label_el.count() > 0:
                    label_text = (await label_el.first.text_content() or "").lower()

                # Sponsorship questions — answer based on applicant info
                if "sponsor" in label_text:
                    target = "Yes" if self.info.sponsorship_needed else "No"
                    try:
                        await select.select_option(label=target, timeout=2000)
                        continue
                    except Exception:
                        pass

                # Authorization questions
                if "authorized" in label_text or "authorization" in label_text or "eligible" in label_text:
                    try:
                        await select.select_option(label="Yes", timeout=2000)
                        continue
                    except Exception:
                        pass

                # "How did you hear about us" — pick first non-empty option
                if "hear" in label_text or "source" in label_text or "how did" in label_text:
                    for j in range(1, opt_count):
                        opt_val = await options.nth(j).get_attribute("value")
                        if opt_val:
                            await select.select_option(index=j, timeout=2000)
                            break
                    continue

                # Gender / Race / Veteran / Disability EEO fields — pick "Decline" or last option
                if any(w in label_text for w in ["gender", "race", "ethnicity", "veteran", "disability", "demographic"]):
                    # Try to find "decline" or "prefer not" option
                    for j in range(opt_count):
                        opt_text = (await options.nth(j).text_content() or "").lower()
                        if "decline" in opt_text or "prefer not" in opt_text or "not disclose" in opt_text:
                            await select.select_option(index=j, timeout=2000)
                            break
                    continue

                # For any other required dropdown, select the first non-empty option
                for j in range(1, opt_count):
                    opt_val = await options.nth(j).get_attribute("value")
                    if opt_val:
                        await select.select_option(index=j, timeout=2000)
                        break

            except Exception:
                continue

        # Fill empty required text inputs that we haven't already filled
        filled_ids = {"first_name", "last_name", "email", "phone"}
        inputs = page.locator('input[type="text"]:visible, input:not([type]):visible')
        input_count = await inputs.count()
        for i in range(input_count):
            inp = inputs.nth(i)
            try:
                inp_id = await inp.get_attribute("id") or ""
                inp_name = await inp.get_attribute("name") or ""
                if inp_id in filled_ids:
                    continue

                # Skip if already has a value
                current = await inp.input_value(timeout=2000)
                if current:
                    continue

                # Check if required
                required = await inp.get_attribute("required")
                aria_required = await inp.get_attribute("aria-required")
                if required is None and aria_required != "true":
                    continue

                # Try to infer the right value from field name/label
                label_el = page.locator(f'label[for="{inp_id}"]')
                label_text = ""
                if await label_el.count() > 0:
                    label_text = (await label_el.first.text_content() or "").lower()

                field_key = (inp_id + inp_name + label_text).lower()

                if "company" in field_key or "employer" in field_key:
                    await inp.fill(self.info.current_company or "Walmart Global Tech", timeout=2000)
                elif "city" in field_key:
                    await inp.fill("Sunnyvale", timeout=2000)
                elif "state" in field_key:
                    await inp.fill("CA", timeout=2000)
                elif "country" in field_key:
                    await inp.fill("United States", timeout=2000)
                elif "salary" in field_key or "compensation" in field_key:
                    await inp.fill("Open to discuss", timeout=2000)
                elif "year" in field_key and "experience" in field_key:
                    await inp.fill("5", timeout=2000)
                elif "github" in field_key:
                    if self.info.github_url:
                        await inp.fill(self.info.github_url, timeout=2000)
                elif "portfolio" in field_key or "website" in field_key:
                    if self.info.portfolio_url:
                        await inp.fill(self.info.portfolio_url, timeout=2000)
            except Exception:
                continue

    async def _submit(self, page: Page) -> None:
        """Submit the application form and verify it went through."""
        pre_url = page.url

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

                # If URL changed after submit, Greenhouse redirected to
                # a thank-you page — application went through
                if page.url != pre_url:
                    logger.info("Confirmed: URL changed after submit (redirected)")
                    return

                # Check for confirmation text on the same page
                page_text = await page.text_content("body") or ""
                page_text_lower = page_text.lower()

                confirmation_phrases = [
                    "thank you", "application has been",
                    "successfully submitted", "received your application",
                    "thanks for applying", "application received",
                ]
                if any(phrase in page_text_lower for phrase in confirmation_phrases):
                    logger.info("Confirmed: application accepted")
                    return

                # Check for visible validation errors (red highlighted fields)
                # Use specific Greenhouse error selectors, only visible ones
                error_msgs = page.locator(
                    '.field-error:visible, '
                    '.field_with_errors:visible, '
                    '.error-message:visible, '
                    '#error_explanation:visible'
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

                # No errors detected, no confirmation — cannot confirm
                raise RuntimeError(
                    "Submit clicked but no confirmation detected — "
                    "application may not have gone through"
                )

        raise RuntimeError("Could not find submit button")
