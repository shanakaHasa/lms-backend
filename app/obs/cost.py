"""Token accounting.

Cost is a production metric here, not a month-end surprise: every answer records
what it cost, and the eval suite reports cost per run alongside quality. That is
what makes "which provider should we default to" an answerable question rather
than a preference.

Prices are USD per million tokens. **Verify them before trusting a number** --
provider pricing changes, and a stale table produces confidently wrong figures.
The Anthropic rates below are from the bundled `claude-api` reference; the
OpenAI rates need confirming against the current pricing page.
"""

from __future__ import annotations

from dataclasses import dataclass

# model -> (input, output, cached_input)
PRICING: dict[str, tuple[float, float, float]] = {
    # Anthropic
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
    # OpenAI -- CONFIRM against current pricing before relying on these.
    "gpt-4o": (2.50, 10.00, 1.25),
    "gpt-4o-mini": (0.15, 0.60, 0.075),
    # Embeddings: input only, so output and cached are zero.
    "text-embedding-3-small": (0.02, 0.0, 0.0),
    "text-embedding-3-large": (0.13, 0.0, 0.0),
}

# Deliberately not zero. An unknown model priced at nothing hides a cost
# regression -- the bill goes up and every dashboard still reads $0.00.
DEFAULT_PRICE = (5.00, 25.00, 0.50)


@dataclass(frozen=True)
class Usage:
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        in_price, out_price, cached_price = PRICING.get(self.model, DEFAULT_PRICE)
        # Cached tokens are billed at the discounted rate and are not also
        # charged as ordinary input.
        billable_input = max(self.input_tokens - self.cached_input_tokens, 0)
        return round(
            (
                billable_input * in_price
                + self.output_tokens * out_price
                + self.cached_input_tokens * cached_price
            )
            / 1_000_000,
            6,
        )

    @property
    def cache_hit_rate(self) -> float:
        return self.cached_input_tokens / self.input_tokens if self.input_tokens else 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def is_priced(model: str) -> bool:
    """Whether the model has a real price rather than the fallback.

    Worth alarming on: a model silently falling back to DEFAULT_PRICE means
    every cost figure for it is a guess.
    """
    return model in PRICING
