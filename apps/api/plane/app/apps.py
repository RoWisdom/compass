# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.apps import AppConfig


class AppApiConfig(AppConfig):
    name = "plane.app"

    def ready(self):
        # Importing registers the wiki-root system checks (``plane.W001`` /
        # ``plane.E001``). Django runs every app's ``ready()`` before it collects
        # checks, so registering here is enough — no ``checks.py`` autoload exists.
        from plane.utils import checks  # noqa: F401
