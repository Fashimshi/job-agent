"""Tests for the submit confirmation logic in all ATS applicants.

Validates that the pre/post text comparison correctly detects:
- True positives: new confirmation text appearing after submit
- True negatives: pre-existing "thank you" text that was there before submit
- URL redirects as confirmation
- Validation errors
- No-confirmation-detected failures
"""

from __future__ import annotations

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from src.application.greenhouse_apply import GreenhouseApplicant
from src.application.lever_apply import LeverApplicant
from src.application.workday_apply import WorkdayApplicant
from src.tracking.models import ApplicantInfo


@pytest.fixture
def applicant_info():
    return ApplicantInfo(
        first_name="Test",
        last_name="User",
        email="test@example.com",
        phone="555-1234",
        linkedin_url="https://linkedin.com/in/test",
    )


def make_mock_page(pre_url, post_url, pre_text, post_text, error_count=0, error_texts=None):
    """Create a mock Playwright page for submit testing."""
    page = AsyncMock()

    # Track URL changes: first call returns pre_url, after click returns post_url
    url_values = [pre_url, post_url]
    url_idx = [0]

    # page.url is a property, not a coroutine
    type(page).url = PropertyMock(side_effect=lambda: url_values[min(url_idx[0], len(url_values) - 1)])

    # text_content returns pre_text first, then post_text after click
    text_values = [pre_text, post_text]
    text_idx = [0]

    async def mock_text_content(selector):
        idx = min(text_idx[0], len(text_values) - 1)
        text_idx[0] += 1
        return text_values[idx]

    page.text_content = mock_text_content

    # wait_for_timeout should advance url/text state
    async def mock_wait(ms):
        url_idx[0] += 1  # After waiting, URL may have changed

    page.wait_for_timeout = mock_wait

    # Mock locator for buttons and errors
    def mock_locator(selector):
        loc = AsyncMock()
        if "error" in selector.lower() or "alert" in selector.lower():
            loc.count = AsyncMock(return_value=error_count)
            if error_texts:
                mock_nths = {}
                for i, txt in enumerate(error_texts):
                    nth_mock = MagicMock()
                    nth_mock.text_content = AsyncMock(return_value=txt)
                    mock_nths[i] = nth_mock
                loc.nth = MagicMock(side_effect=lambda idx: mock_nths.get(idx, MagicMock()))
        elif "confirmation" in selector.lower():
            loc.count = AsyncMock(return_value=0)
        else:
            # Submit button
            loc.first = AsyncMock()
            loc.first.count = AsyncMock(return_value=1)
            loc.first.click = AsyncMock()
            loc.count = AsyncMock(return_value=1)
            loc.click = AsyncMock()
        return loc

    page.locator = mock_locator
    return page


# =====================================================================
# Greenhouse Tests
# =====================================================================

class TestGreenhouseSubmit:
    """Test Greenhouse _submit confirmation logic."""

    @pytest.mark.asyncio
    async def test_url_redirect_with_confirmation_text_succeeds(self, applicant_info):
        """URL change + confirmation text on new page = confirmed success."""
        page = make_mock_page(
            pre_url="https://boards.greenhouse.io/company/jobs/123",
            post_url="https://boards.greenhouse.io/company/jobs/123/thank_you",
            pre_text="Apply for this role.",
            post_text="Thanks for applying! We received your application.",
        )
        applicant = GreenhouseApplicant(applicant_info)
        await applicant._submit(page)

    @pytest.mark.asyncio
    async def test_url_redirect_without_confirmation_text_fails(self, applicant_info):
        """URL change but no confirmation text on new page = not reliable."""
        page = make_mock_page(
            pre_url="https://boards.greenhouse.io/company/jobs/123",
            post_url="https://boards.greenhouse.io/login",
            pre_text="Apply for this role.",
            post_text="Please log in to continue.",
        )
        applicant = GreenhouseApplicant(applicant_info)
        with pytest.raises(RuntimeError, match="no confirmation detected"):
            await applicant._submit(page)

    @pytest.mark.asyncio
    async def test_preexisting_thank_you_not_false_positive(self, applicant_info):
        """Pre-existing 'thank you' text should NOT trigger false positive."""
        page = make_mock_page(
            pre_url="https://boards.greenhouse.io/company/jobs/123",
            post_url="https://boards.greenhouse.io/company/jobs/123",  # No URL change
            pre_text="Thank you for considering this role. We value your time.",
            post_text="Thank you for considering this role. We value your time.",  # Same text
        )
        applicant = GreenhouseApplicant(applicant_info)
        with pytest.raises(RuntimeError, match="no confirmation detected"):
            await applicant._submit(page)

    @pytest.mark.asyncio
    async def test_new_confirmation_text_after_submit(self, applicant_info):
        """New confirmation text appearing after submit = success."""
        page = make_mock_page(
            pre_url="https://boards.greenhouse.io/company/jobs/123",
            post_url="https://boards.greenhouse.io/company/jobs/123",  # No URL change
            pre_text="Apply for Software Engineer at Acme Corp.",
            post_text="Apply for Software Engineer at Acme Corp. Thanks for applying! We received your application.",
        )
        applicant = GreenhouseApplicant(applicant_info)
        # "thanks for applying" is new — should confirm
        await applicant._submit(page)

    @pytest.mark.asyncio
    async def test_validation_errors_raise(self, applicant_info):
        """Visible validation errors should raise RuntimeError."""
        page = make_mock_page(
            pre_url="https://boards.greenhouse.io/company/jobs/123",
            post_url="https://boards.greenhouse.io/company/jobs/123",
            pre_text="Apply here.",
            post_text="Apply here.",
            error_count=1,
            error_texts=["This field is required"],
        )
        applicant = GreenhouseApplicant(applicant_info)
        with pytest.raises(RuntimeError, match="validation errors"):
            await applicant._submit(page)

    @pytest.mark.asyncio
    async def test_no_submit_button_raises(self, applicant_info):
        """Missing submit button should raise RuntimeError."""
        page = AsyncMock()
        page.text_content = AsyncMock(return_value="Some page text")
        type(page).url = PropertyMock(return_value="https://boards.greenhouse.io/company/jobs/123")

        # All button locators return count=0
        def mock_locator(selector):
            loc = AsyncMock()
            loc.first = AsyncMock()
            loc.first.count = AsyncMock(return_value=0)
            loc.count = AsyncMock(return_value=0)
            return loc

        page.locator = mock_locator
        applicant = GreenhouseApplicant(applicant_info)
        with pytest.raises(RuntimeError, match="Could not find submit button"):
            await applicant._submit(page)

    @pytest.mark.asyncio
    async def test_application_has_been_submitted_new_text(self, applicant_info):
        """'application has been submitted' appearing after submit = success."""
        page = make_mock_page(
            pre_url="https://boards.greenhouse.io/company/jobs/123",
            post_url="https://boards.greenhouse.io/company/jobs/123",
            pre_text="Fill in the form below.",
            post_text="Fill in the form below. Your application has been submitted successfully.",
        )
        applicant = GreenhouseApplicant(applicant_info)
        await applicant._submit(page)

    @pytest.mark.asyncio
    async def test_footer_thank_you_ignored(self, applicant_info):
        """A 'thanks for your interest' in footer before AND after submit should not confirm."""
        footer_text = "thanks for your interest in our company"
        page = make_mock_page(
            pre_url="https://boards.greenhouse.io/company/jobs/123",
            post_url="https://boards.greenhouse.io/company/jobs/123",
            pre_text=f"Apply now. {footer_text}",
            post_text=f"Apply now. {footer_text}",  # Same footer, no new text
        )
        applicant = GreenhouseApplicant(applicant_info)
        with pytest.raises(RuntimeError, match="no confirmation detected"):
            await applicant._submit(page)


# =====================================================================
# Lever Tests
# =====================================================================

class TestLeverSubmit:
    """Test Lever _submit confirmation logic."""

    @pytest.mark.asyncio
    async def test_url_redirect_with_confirmation_text_succeeds(self, applicant_info):
        """URL change + confirmation text on new page = success."""
        page = make_mock_page(
            pre_url="https://jobs.lever.co/company/abc123/apply",
            post_url="https://jobs.lever.co/company/abc123/thanks",
            pre_text="Apply here.",
            post_text="Thanks for applying! We received your application.",
        )
        applicant = LeverApplicant(applicant_info)
        await applicant._submit(page)

    @pytest.mark.asyncio
    async def test_url_redirect_without_confirmation_text_fails(self, applicant_info):
        """URL change but no confirmation text = not reliable."""
        page = make_mock_page(
            pre_url="https://jobs.lever.co/company/abc123/apply",
            post_url="https://jobs.lever.co/company/abc123/error",
            pre_text="Apply here.",
            post_text="Something went wrong.",
        )
        applicant = LeverApplicant(applicant_info)
        with pytest.raises(RuntimeError, match="no confirmation detected"):
            await applicant._submit(page)

    @pytest.mark.asyncio
    async def test_preexisting_text_no_false_positive(self, applicant_info):
        page = make_mock_page(
            pre_url="https://jobs.lever.co/company/abc123/apply",
            post_url="https://jobs.lever.co/company/abc123/apply",
            pre_text="Thank you for your interest in joining us. Apply below.",
            post_text="Thank you for your interest in joining us. Apply below.",
        )
        applicant = LeverApplicant(applicant_info)
        with pytest.raises(RuntimeError, match="no confirmation detected"):
            await applicant._submit(page)

    @pytest.mark.asyncio
    async def test_new_received_your_application_text(self, applicant_info):
        page = make_mock_page(
            pre_url="https://jobs.lever.co/company/abc123/apply",
            post_url="https://jobs.lever.co/company/abc123/apply",
            pre_text="Apply for this role.",
            post_text="Apply for this role. We received your application and will be in touch.",
        )
        applicant = LeverApplicant(applicant_info)
        await applicant._submit(page)

    @pytest.mark.asyncio
    async def test_no_submit_button_raises(self, applicant_info):
        page = AsyncMock()
        page.text_content = AsyncMock(return_value="Some page")
        type(page).url = PropertyMock(return_value="https://jobs.lever.co/x")

        def mock_locator(selector):
            loc = AsyncMock()
            loc.first = AsyncMock()
            loc.first.count = AsyncMock(return_value=0)
            loc.count = AsyncMock(return_value=0)
            return loc

        page.locator = mock_locator
        applicant = LeverApplicant(applicant_info)
        with pytest.raises(RuntimeError, match="Could not find submit button"):
            await applicant._submit(page)


# =====================================================================
# Workday Tests
# =====================================================================

class TestWorkdaySubmit:
    """Test Workday _final_submit confirmation logic."""

    @pytest.mark.asyncio
    async def test_url_redirect_with_confirmation_text_succeeds(self, applicant_info):
        """URL change + confirmation text on new page = success."""
        page = make_mock_page(
            pre_url="https://company.wd5.myworkdayjobs.com/en-US/careers/job/apply",
            post_url="https://company.wd5.myworkdayjobs.com/en-US/careers/job/thank-you",
            pre_text="Review your application.",
            post_text="Your application has been submitted successfully!",
        )
        applicant = WorkdayApplicant(applicant_info)
        await applicant._final_submit(page)

    @pytest.mark.asyncio
    async def test_url_redirect_without_confirmation_text_fails(self, applicant_info):
        """URL change but no confirmation text = not reliable."""
        page = make_mock_page(
            pre_url="https://company.wd5.myworkdayjobs.com/apply",
            post_url="https://company.wd5.myworkdayjobs.com/login",
            pre_text="Review your application.",
            post_text="Sign in to continue.",
        )
        applicant = WorkdayApplicant(applicant_info)
        with pytest.raises(RuntimeError, match="no confirmation detected"):
            await applicant._final_submit(page)

    @pytest.mark.asyncio
    async def test_preexisting_text_no_false_positive(self, applicant_info):
        page = make_mock_page(
            pre_url="https://company.wd5.myworkdayjobs.com/apply",
            post_url="https://company.wd5.myworkdayjobs.com/apply",
            pre_text="Thanks for your interest in our company. Fill the form.",
            post_text="Thanks for your interest in our company. Fill the form.",
        )
        applicant = WorkdayApplicant(applicant_info)
        with pytest.raises(RuntimeError, match="no confirmation detected"):
            await applicant._final_submit(page)

    @pytest.mark.asyncio
    async def test_new_application_submitted_text(self, applicant_info):
        page = make_mock_page(
            pre_url="https://company.wd5.myworkdayjobs.com/apply",
            post_url="https://company.wd5.myworkdayjobs.com/apply",
            pre_text="Complete your application.",
            post_text="Complete your application. Your application is submitted!",
        )
        applicant = WorkdayApplicant(applicant_info)
        await applicant._final_submit(page)

    @pytest.mark.asyncio
    async def test_validation_errors_raise(self, applicant_info):
        page = make_mock_page(
            pre_url="https://company.wd5.myworkdayjobs.com/apply",
            post_url="https://company.wd5.myworkdayjobs.com/apply",
            pre_text="Fill form.",
            post_text="Fill form.",
            error_count=1,
            error_texts=["Required field is missing"],
        )
        applicant = WorkdayApplicant(applicant_info)
        with pytest.raises(RuntimeError, match="validation errors"):
            await applicant._final_submit(page)
