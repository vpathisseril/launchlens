"""Hub registry — the five research hubs, ordered by 2026 market demand."""
from app.hubs.access import AccessHub
from app.hubs.base import Hub
from app.hubs.differentiation import DifferentiationHub
from app.hubs.policy import PolicyHub
from app.hubs.pricing import PricingHub
from app.hubs.targeting import TargetingHub

HUBS: dict[str, Hub] = {h.info.key: h for h in (AccessHub(), PricingHub(), PolicyHub(), TargetingHub(),
                                               DifferentiationHub())}


def get_hub(key: str) -> Hub | None:
    return HUBS.get(key)
