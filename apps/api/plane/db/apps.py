# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.apps import AppConfig


class DbConfig(AppConfig):
    name = "plane.db"

    def ready(self):
        # Signal handlers are only registered by importing them (`plane.app` does
        # the same for its checks). Keep the import here, not at module scope, so
        # the app registry is populated first.
        from plane.db.signals import agent_approval  # noqa: F401
