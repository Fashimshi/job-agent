from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable, TypeVar

import anthropic
import openai

if TYPE_CHECKING:
    from src.config_loader import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class LLMClient:
    """Unified LLM client with OpenAI primary, Anthropic fallback."""

    # Rate limiting: max concurrent requests
    MAX_CONCURRENT_REQUESTS = 10
    # Retry settings
    MAX_RETRIES = 3
    BASE_DELAY = 2.0  # seconds

    def __init__(self, settings: Settings):
        self.settings = settings
        self._openai: openai.AsyncOpenAI | None = None
        self._anthropic: anthropic.AsyncAnthropic | None = None
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REQUESTS)

        if settings.openai_api_key:
            self._openai = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        if settings.anthropic_api_key:
            self._anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def _retry_with_backoff(
        self,
        func: Callable[[], T],
        max_retries: int | None = None,
        base_delay: float | None = None,
    ) -> T:
        """Execute a function with exponential backoff retry."""
        max_retries = max_retries or self.MAX_RETRIES
        base_delay = base_delay or self.BASE_DELAY

        last_exception: Exception | None = None
        for attempt in range(max_retries):
            try:
                return await func()
            except Exception as e:
                last_exception = e
                if attempt == max_retries - 1:
                    raise

                # Check for rate limit errors and use longer backoff
                delay = base_delay * (2 ** attempt)
                if self._is_rate_limit_error(e):
                    delay = max(delay, 10.0)  # At least 10 seconds for rate limits
                    logger.warning(f"Rate limit hit, waiting {delay}s before retry...")
                else:
                    logger.warning(
                        f"Attempt {attempt + 1}/{max_retries} failed: {e}. "
                        f"Retrying in {delay}s..."
                    )
                await asyncio.sleep(delay)

        # Should not reach here, but just in case
        raise last_exception or RuntimeError("Retry failed")

    @staticmethod
    def _is_rate_limit_error(e: Exception) -> bool:
        """Check if an exception is a rate limit error."""
        error_str = str(e).lower()
        return any(
            term in error_str
            for term in ["rate limit", "rate_limit", "429", "too many requests"]
        )

    async def complete(
        self,
        prompt: str,
        model: str | None = None,
        max_tokens: int = 1000,
    ) -> str:
        """Send a completion request with rate limiting and retry. Tries primary provider, falls back."""
        model = model or self.settings.matching.scoring_model

        async with self._semaphore:
            # Try primary provider with retry
            try:
                if self.settings.matching.primary_provider == "openai" and self._openai:
                    return await self._retry_with_backoff(
                        lambda: self._openai_complete(prompt, model, max_tokens)
                    )
                elif self._anthropic:
                    return await self._retry_with_backoff(
                        lambda: self._anthropic_complete(prompt, model, max_tokens)
                    )
            except Exception as e:
                logger.warning(f"Primary LLM ({self.settings.matching.primary_provider}) failed after retries: {e}")

            # Try fallback provider with retry
            try:
                if self.settings.matching.fallback_provider == "anthropic" and self._anthropic:
                    fallback_model = self.settings.matching.scoring_model_fallback
                    return await self._retry_with_backoff(
                        lambda: self._anthropic_complete(prompt, fallback_model, max_tokens)
                    )
                elif self._openai:
                    fallback_model = self.settings.matching.scoring_model
                    return await self._retry_with_backoff(
                        lambda: self._openai_complete(prompt, fallback_model, max_tokens)
                    )
            except Exception as e:
                logger.error(f"Fallback LLM also failed after retries: {e}")
                raise

            raise RuntimeError("No LLM provider configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY.")

    async def complete_cover_letter(self, prompt: str, max_tokens: int = 1500) -> str:
        """Use the higher-quality model for cover letters with rate limiting and retry."""
        model = self.settings.matching.cover_letter_model

        async with self._semaphore:
            try:
                if self.settings.matching.primary_provider == "openai" and self._openai:
                    return await self._retry_with_backoff(
                        lambda: self._openai_complete(prompt, model, max_tokens)
                    )
                elif self._anthropic:
                    return await self._retry_with_backoff(
                        lambda: self._anthropic_complete(prompt, model, max_tokens)
                    )
            except Exception as e:
                logger.warning(f"Primary cover letter model failed after retries: {e}")

            # Fallback with retry
            fallback_model = self.settings.matching.cover_letter_model_fallback
            if self._anthropic:
                return await self._retry_with_backoff(
                    lambda: self._anthropic_complete(prompt, fallback_model, max_tokens)
                )
            if self._openai:
                return await self._retry_with_backoff(
                    lambda: self._openai_complete(prompt, self.settings.matching.cover_letter_model, max_tokens)
                )
            raise RuntimeError("No LLM provider available for cover letter generation.")

    async def _openai_complete(self, prompt: str, model: str, max_tokens: int) -> str:
        assert self._openai is not None
        response = await self._openai.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return response.choices[0].message.content or ""

    async def _anthropic_complete(self, prompt: str, model: str, max_tokens: int) -> str:
        assert self._anthropic is not None
        response = await self._anthropic.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return response.content[0].text
