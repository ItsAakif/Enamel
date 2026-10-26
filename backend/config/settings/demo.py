"""Hosted public demo settings.

The public demo of this repository is served by
``deploy/docker-compose.demo.yml`` and deliberately does NOT claim to be the
production profile: it runs a dedicated instance seeded with synthetic data
(``seed_demo``) and with ``DEMO_MODE``, which makes the portal request-code
response carry the generated code in an explicitly labelled ``demo_code``
field, so a visitor can actually sign in to the portal.

Everything else is the hardened production posture, imported unmodified from
``config.settings.prod`` (fail-closed secrets, HSTS, CSP, secure cookies, the
row-level-security contract). That module refuses ``DEMO_MODE`` - the
refusal is exactly what keeps the production profile fail-closed against an
operator error - so the flag is hidden from that import and asserted back
afterwards. Nothing else about the production posture changes here.
"""

from __future__ import annotations

import os

# The production module reads DEMO_MODE directly from the environment and
# refuses to import while it is set. The demo profile is the one deployment
# allowed to enable it; hide the flag from that import, then restore it and
# state DEMO_MODE explicitly after the import.
_saved_demo_mode = os.environ.pop("DEMO_MODE", None)
os.environ["DEMO_MODE"] = ""

from .prod import *  # noqa: E402,F403
from .prod import (  # noqa: E402
    CONNECTOR_KEY,
    MESSAGING_WEBHOOK_SECRET,
    PAYMENT_WEBHOOK_SECRET,
)

os.environ["DEMO_MODE"] = _saved_demo_mode or "true"

DEMO_MODE = True

# Cross-check the secrets survived the star import (they are consumed by the
# webhook views through settings attributes, so an import quirk here would
# only surface in production behaviour).
assert PAYMENT_WEBHOOK_SECRET, "demo module lost PAYMENT_WEBHOOK_SECRET"
assert MESSAGING_WEBHOOK_SECRET, "demo module lost MESSAGING_WEBHOOK_SECRET"
assert CONNECTOR_KEY, "demo module lost CONNECTOR_KEY"
