"""Settings package.

Select an environment with ``DJANGO_SETTINGS_MODULE``:

- ``config.settings.dev``  (manage.py default)
- ``config.settings.test`` (pytest)
- ``config.settings.prod`` (containers)

``<repo>/.env`` is loaded once here, before any environment-specific module sets
development defaults, so shell variables win over ``.env`` and ``.env`` wins over
defaults.
"""

from pathlib import Path

try:  # pragma: no cover - optional local convenience
    from dotenv import load_dotenv

    _REPO_ROOT = Path(__file__).resolve().parents[3]
    load_dotenv(_REPO_ROOT / ".env", override=False)
except ImportError:  # pragma: no cover
    pass
