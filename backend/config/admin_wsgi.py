"""The WSGI entry point of the platform-admin service (ADR-013, D-1).

It is the same Django application, but it serves the **platform-admin URL
surface** only (``config.admin_urls``): the Django admin and the liveness
probe, never the clinic-facing API. The public web service does not proxy
this port, so the admin is reachable on its own route/port and with its own
credential.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
# The admin surface has its own URLconf; the setting is read when the
# application is created, so it has to be set before that.
os.environ["ROOT_URLCONF"] = "config.admin_urls"

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
