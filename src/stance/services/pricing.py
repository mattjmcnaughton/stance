"""Launch-model pricing preflight against Omnigent's model catalog."""

from omnigent.llms.context_window import fetch_model_pricing

# Private, pinned to omnigent==0.20.0: the exact catalog targets upstream
# derives for a model id (including Bedrock ``us.anthropic.claude-...-v1:0``
# normalization). Used only to tell an exact match from a family estimate.
from omnigent.onboarding.providers import _catalog_lookup_targets, find_catalog_models


class PricingError(Exception):
    """The launch model has no exact, priced catalog entry."""


def _tail(name: str) -> str:
    return name.rsplit("/", 1)[-1].lower()


def check_model_pricing(model: str) -> None:
    """Require an exact, priced catalog entry for *model*.

    Upstream pricing also accepts an unambiguous family-prefix estimate (for
    example ``claude-sonnet-5-5-UNRECOGNIZED`` prices as ``claude-sonnet-5-5``).
    Stance rejects those rather than guess.
    """
    matches = find_catalog_models(model)
    lookup_ids = {_tail(lookup_id) for _, lookup_id in _catalog_lookup_targets(model)}
    exact = len(matches) == 1 and _tail(matches[0].name) in lookup_ids
    if not exact:
        hint = (
            f" Closest catalog entries: {', '.join(m.name for m in matches[:3])}."
            if matches
            else ""
        )
        raise PricingError(
            f"Model {model!r} has no exact entry in Omnigent's pricing catalog, so "
            f"Stance cannot budget it.{hint} Pass a full model id with --model "
            "(for example claude-sonnet-5-5, us.anthropic.claude-sonnet-5-5-v1:0, "
            "or gpt-6.1-sol). If the catalog could not be downloaded, check network "
            "access to github.com and retry."
        )
    if fetch_model_pricing(model) is None:
        raise PricingError(
            f"Model {model!r} is in Omnigent's catalog but has no published "
            "input/output pricing, so Stance cannot budget it."
        )
