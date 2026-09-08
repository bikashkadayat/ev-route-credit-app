"""Provider selection — Doc 02 §2.17, Doc 04 §4.11.

Switching between mock and live is an environment variable. No service or engine code
changes, which is what makes the demo runnable without a single third-party contract.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import date

from app.integrations.cib.base import CIBProvider
from app.integrations.cib.mock import MockCIBProvider
from app.integrations.telematics.base import TelematicsProvider
from app.integrations.telematics.mock import MockTelematicsProvider, demo_profiles


def build_cib_provider(provider: str | None = None) -> CIBProvider:
    choice = (provider or os.getenv("CIB_PROVIDER", "mock")).lower()
    if choice == "mock":
        validity = int(os.getenv("CIB_REPORT_VALIDITY_DAYS", "90"))
        return MockCIBProvider(validity_days=validity)
    if choice == "live":
        raise NotImplementedError(
            "LiveCIBProvider is not implemented: it requires a signed bureau contract and "
            "credentials. Implement app/integrations/cib/live.py against CIBProvider - the "
            "interface is the only thing the services depend on."
        )
    raise ValueError(f"Unknown CIB_PROVIDER {choice!r} (expected 'mock' or 'live')")


def build_telematics_provider(
    provider: str | None = None,
    *,
    anchor: date | None = None,
    profiles: Sequence | None = None,
) -> TelematicsProvider:
    choice = (provider or os.getenv("TELEMATICS_PROVIDER", "mock")).lower()
    if choice == "mock":
        seed = os.getenv("DEMO_SEED", "20260908")
        resolved = (
            tuple(profiles) if profiles is not None
            else demo_profiles(anchor or date.today())
        )
        return MockTelematicsProvider(resolved, seed=seed)
    if choice == "live":
        raise NotImplementedError(
            "LiveTelematicsProvider is not implemented: it requires an OEM or OBD platform "
            "contract. Implement app/integrations/telematics/live.py against "
            "TelematicsProvider."
        )
    raise ValueError(f"Unknown TELEMATICS_PROVIDER {choice!r} (expected 'mock' or 'live')")
