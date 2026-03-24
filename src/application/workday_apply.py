from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import Page, async_playwright, TimeoutError as PWTimeout

from src.application.base_applicant import BaseApplicant
from src.tracking.models import ApplicantInfo, ApplicationResult, Job

logger = logging.getLogger(__name__)

# Workday uses data-automation-id attributes consistently across all instances
SEL = {
    "apply_btn": '[data-automation-id="jobAction-apply"], a:has-text("Apply")',
    "apply_manually": 'a:has-text("Apply Manually"), button:has-text("Apply Manually")',
    "create_account": 'a:has-text("Create Account"), button:has-text("Create Account")',
    "sign_in": 'a:has-text("Sign In"), button:has-text("Sign In")',
    "email_input": '[data-automation-id="email"], input[type="email"]',
    "password_input": '[data-automation-id="password"], input[type="password"]',
    "create_password": '[data-automation-id="createAccountSubmitButton"], button:has-text("Create Account")',
    "verify_btn": 'button:has-text("Verify"), button:has-text("Submit")',
    "file_upload": '[data-automation-id="file-upload-input-ref"], input[type="file"]',
    "use_resume": 'button:has-text("Use My Last Application"), button:has-text("Autofill")',
    "next_btn": '[data-automation-id="bottom-navigation-next-button"], button:has-text("Next"), button:has-text("Continue")',
    "submit_btn": '[data-automation-id="submit-button"], button:has-text("Submit Application"), button:has-text("Submit")',
    "review_btn": 'button:has-text("Review"), button:has-text("Review and Submit")',
    # Form fields
    "first_name": '[data-automation-id="legalNameSection_firstName"], input[aria-label*="First Name" i]',
    "last_name": '[data-automation-id="legalNameSection_lastName"], input[aria-label*="Last Name" i]',
    "phone": '[data-automation-id="phone-number"], input[aria-label*="Phone" i]',
    "address_line1": '[data-automation-id="addressSection_addressLine1"]',
    "city": '[data-automation-id="addressSection_city"]',
    "state": '[data-automation-id="addressSection_countryRegion"]',
    "postal_code": '[data-automation-id="addressSection_postalCode"]',
    "linkedin": 'input[aria-label*="LinkedIn" i], input[placeholder*="LinkedIn" i]',
    # Work authorization
    "auth_yes": 'label:has-text("authorized to work")',
    "sponsorship_no": 'label:has-text("not require sponsorship")',
}

# Max pages to walk through before giving up
MAX_STEPS = 8


class WorkdayApplicant(BaseApplicant):
    """Auto-fill and submit Workday application forms using resume autofill."""

    async def apply(
        self,
        job: Job,
        cover_letter: str,
        resume_path: Path,
        screenshot_dir: Path,
        dry_run: bool = True,
    ) -> ApplicationResult:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / f"workday_{job.id}.png"

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

                # Step 1: Navigate to the job page
                await page.goto(job.apply_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)

                # Step 2: Click "Apply" to start application
                await self._click_apply(page)

                # Step 3: Handle account creation / sign in
                await self._handle_auth(page)

                # Step 4: Upload resume for autofill
                await self._upload_resume(page, resume_path)

                # Step 5: Walk through form steps, filling gaps
                await self._walk_form_steps(page, cover_letter, screenshot_dir, job.id)

                # Final screenshot
                await page.screenshot(path=str(screenshot_path), full_page=True)
                logger.info(f"Screenshot saved: {screenshot_path}")

                # Step 6: Submit or stop at dry run
                if not dry_run:
                    await self._final_submit(page)
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
            logger.error(f"Workday apply failed for {job.title} at {job.company}: {e}")
            return ApplicationResult(
                success=False,
                job_id=job.id,
                screenshot_path=str(screenshot_path) if screenshot_path.exists() else None,
                error_message=str(e),
            )

    async def _click_apply(self, page: Page) -> None:
        """Click the Apply button on the job listing page."""
        # Try the main apply button
        apply = page.locator(SEL["apply_btn"]).first
        if await apply.count() > 0:
            await apply.click()
            await page.wait_for_timeout(3000)
            return

        # Some pages go straight to the application form
        if await page.locator(SEL["file_upload"]).count() > 0:
            return  # Already on the form

        # Try "Apply Manually" link
        manual = page.locator(SEL["apply_manually"]).first
        if await manual.count() > 0:
            await manual.click()
            await page.wait_for_timeout(3000)
            return

        raise RuntimeError("Could not find Apply button on Workday job page")

    async def _handle_auth(self, page: Page) -> None:
        """Handle Workday sign-in or account creation."""
        await page.wait_for_timeout(2000)

        # Check if we're on a sign-in / create account page
        create_btn = page.locator(SEL["create_account"]).first
        sign_in_btn = page.locator(SEL["sign_in"]).first

        if await create_btn.count() > 0:
            # Try "Create Account" flow
            await create_btn.click()
            await page.wait_for_timeout(2000)

            # Fill email
            email_input = page.locator(SEL["email_input"]).first
            if await email_input.count() > 0:
                await email_input.fill(self.info.email)

            # Fill password (use a standard password)
            password_input = page.locator(SEL["password_input"]).first
            if await password_input.count() > 0:
                await password_input.fill("AutoApply2026!")

            # Confirm password if there's a second field
            confirm_pw = page.locator('[data-automation-id="verifyPassword"], input[aria-label*="Confirm" i]').first
            if await confirm_pw.count() > 0:
                await confirm_pw.fill("AutoApply2026!")

            # Submit account creation
            submit = page.locator(SEL["create_password"]).first
            if await submit.count() > 0:
                await submit.click()
                await page.wait_for_timeout(3000)

        elif await sign_in_btn.count() > 0:
            # Sign in with existing account
            await sign_in_btn.click()
            await page.wait_for_timeout(2000)

            email_input = page.locator(SEL["email_input"]).first
            if await email_input.count() > 0:
                await email_input.fill(self.info.email)

            password_input = page.locator(SEL["password_input"]).first
            if await password_input.count() > 0:
                await password_input.fill("AutoApply2026!")

            submit = page.locator('button[type="submit"], button:has-text("Sign In")').first
            if await submit.count() > 0:
                await submit.click()
                await page.wait_for_timeout(3000)

        # If neither button found, we might already be past auth

    async def _upload_resume(self, page: Page, resume_path: Path) -> None:
        """Upload resume to trigger Workday's autofill."""
        file_input = page.locator(SEL["file_upload"]).first

        if await file_input.count() > 0:
            await file_input.set_input_files(str(resume_path))
            logger.info("Resume uploaded — waiting for Workday autofill")
            # Workday takes a few seconds to parse the resume and autofill
            await page.wait_for_timeout(5000)
        else:
            logger.warning("No file upload found on current page")

        # Click "Use My Last Application" if available (returning applicants)
        use_last = page.locator(SEL["use_resume"]).first
        if await use_last.count() > 0:
            await use_last.click()
            await page.wait_for_timeout(3000)

    async def _walk_form_steps(
        self, page: Page, cover_letter: str, screenshot_dir: Path, job_id: str
    ) -> None:
        """Walk through Workday's multi-step form, filling gaps after autofill."""
        for step in range(MAX_STEPS):
            logger.info(f"Workday form step {step + 1}")

            # Fill any empty fields on the current page
            await self._fill_current_page(page, cover_letter)

            # Screenshot each step
            step_screenshot = screenshot_dir / f"workday_{job_id}_step{step + 1}.png"
            await page.screenshot(path=str(step_screenshot), full_page=True)

            # Check if we're on the review/submit page
            submit = page.locator(SEL["submit_btn"]).first
            review = page.locator(SEL["review_btn"]).first
            if await submit.count() > 0 or await review.count() > 0:
                logger.info("Reached review/submit page")
                return

            # Click Next/Continue
            next_btn = page.locator(SEL["next_btn"]).first
            if await next_btn.count() > 0:
                await next_btn.click()
                await page.wait_for_timeout(3000)

                # Check for validation errors
                errors = page.locator('[data-automation-id="errorMessage"], .css-1blqw1b')
                if await errors.count() > 0:
                    error_text = await errors.first.text_content()
                    logger.warning(f"Validation error on step {step + 1}: {error_text}")
            else:
                # No next button — might be single-page or we're done
                logger.info("No next button found — form may be complete")
                return

    async def _fill_current_page(self, page: Page, cover_letter: str) -> None:
        """Fill any empty fields on the current Workday form page."""

        # Name fields (fill only if empty)
        await self._fill_if_empty(page, SEL["first_name"], self.info.first_name)
        await self._fill_if_empty(page, SEL["last_name"], self.info.last_name)

        # Phone
        await self._fill_if_empty(page, SEL["phone"], self.info.phone)

        # Email
        email_field = page.locator(
            '[data-automation-id="email"], '
            'input[aria-label*="Email" i]:not([type="hidden"])'
        ).first
        if await email_field.count() > 0:
            val = await email_field.input_value()
            if not val:
                await email_field.fill(self.info.email)

        # LinkedIn
        await self._fill_if_empty(page, SEL["linkedin"], self.info.linkedin_url)

        # Location fields
        await self._fill_if_empty(page, SEL["address_line1"], self.info.location)

        # Cover letter — look for textarea
        cover_textarea = page.locator(
            'textarea[aria-label*="cover letter" i], '
            'textarea[aria-label*="additional information" i], '
            'textarea[data-automation-id*="cover" i]'
        ).first
        if await cover_textarea.count() > 0:
            val = await cover_textarea.input_value()
            if not val:
                await cover_textarea.fill(cover_letter)

        # Work authorization questions — try to select "Yes" for authorized
        await self._handle_dropdowns(page)
        await self._handle_radio_buttons(page)

    async def _fill_if_empty(self, page: Page, selector: str, value: str) -> None:
        """Fill a field only if it exists and is currently empty."""
        if not value:
            return
        field = page.locator(selector).first
        if await field.count() > 0:
            try:
                current = await field.input_value()
                if not current:
                    await field.fill(value)
            except Exception:
                pass

    async def _handle_dropdowns(self, page: Page) -> None:
        """Handle Workday custom dropdown questions (work auth, sponsorship, etc.)."""
        # Workday uses custom dropdowns, not standard <select>
        # Look for common question patterns and try to answer them
        dropdowns = page.locator('button[aria-haspopup="listbox"]')
        count = await dropdowns.count()

        for i in range(count):
            dropdown = dropdowns.nth(i)
            label = await dropdown.get_attribute("aria-label") or ""
            label_lower = label.lower()

            # Work authorization
            if "authorized" in label_lower or "eligible" in label_lower:
                await self._select_dropdown_option(page, dropdown, "Yes")
            # Sponsorship
            elif "sponsor" in label_lower:
                if self.info.sponsorship_needed:
                    await self._select_dropdown_option(page, dropdown, "Yes")
                else:
                    await self._select_dropdown_option(page, dropdown, "No")

    async def _select_dropdown_option(self, page: Page, dropdown, option_text: str) -> None:
        """Open a Workday dropdown and select an option by text."""
        try:
            await dropdown.click()
            await page.wait_for_timeout(500)
            option = page.locator(f'[role="option"]:has-text("{option_text}")').first
            if await option.count() > 0:
                await option.click()
                await page.wait_for_timeout(500)
            else:
                # Close dropdown if option not found
                await page.keyboard.press("Escape")
        except Exception as e:
            logger.debug(f"Failed to select dropdown option '{option_text}': {e}")

    async def _handle_radio_buttons(self, page: Page) -> None:
        """Handle radio button questions on Workday forms."""
        # Look for fieldsets or groups with radio buttons
        radio_groups = page.locator('fieldset, [role="radiogroup"]')
        count = await radio_groups.count()

        for i in range(count):
            group = radio_groups.nth(i)
            legend = group.locator("legend, label").first
            if await legend.count() == 0:
                continue

            text = (await legend.text_content() or "").lower()

            if "authorized" in text or "legally" in text or "eligible" in text:
                yes_radio = group.locator('label:has-text("Yes"), input[value="Yes"]').first
                if await yes_radio.count() > 0:
                    await yes_radio.click()
            elif "sponsor" in text:
                target = "Yes" if self.info.sponsorship_needed else "No"
                radio = group.locator(f'label:has-text("{target}"), input[value="{target}"]').first
                if await radio.count() > 0:
                    await radio.click()

    async def _final_submit(self, page: Page) -> None:
        """Click the final submit button and verify it went through."""
        pre_url = page.url

        # Capture page text BEFORE submit so we can detect NEW confirmation text
        pre_text = (await page.text_content("body") or "").lower()

        # Try review first
        review = page.locator(SEL["review_btn"]).first
        if await review.count() > 0:
            await review.click()
            await page.wait_for_timeout(2000)

        # Now submit
        submit = page.locator(SEL["submit_btn"]).first
        if await submit.count() > 0:
            await submit.click()
            await page.wait_for_timeout(5000)
        else:
            raise RuntimeError("Could not find submit button on Workday form")

        # 1. URL change — Workday redirects on success
        if page.url != pre_url:
            logger.info("Confirmed: URL changed after submit (redirected)")
            return

        # 2. Confirmation text that appeared AFTER submit
        # (ignore text already on the page before clicking)
        post_text = (await page.text_content("body") or "").lower()
        confirmation_phrases = [
            "application has been submitted",
            "successfully submitted", "received your application",
            "thanks for applying", "application received",
            "thanks for your interest", "application is submitted",
            "your application has been",
        ]
        for phrase in confirmation_phrases:
            if phrase in post_text and phrase not in pre_text:
                logger.info(f"Confirmed: new confirmation text appeared: '{phrase}'")
                return

        # 3. Visible validation errors — Workday error selectors
        error_msgs = page.locator(
            '[data-automation-id="errorMessage"]:visible, '
            '.css-1blqw1b:visible, '
            '[data-automation-id="formErrorBanner"]:visible, '
            '[role="alert"]:visible'
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
