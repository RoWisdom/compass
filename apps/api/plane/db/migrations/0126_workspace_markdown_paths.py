# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.db import migrations, models


class Migration(migrations.Migration):
    """给 `Workspace` 加两个镜像根字段（罗盘：Workspace 目录配置）。

    **additive、nullable、无默认值** ⇒ 对存量行安全（`ADD COLUMN` 全 NULL），
    按 CLAUDE.md 的既定规矩不需要用户确认。

    依赖写 `0125_page_node_type` —— 那是 `ls` 出来的真实 head 名。
    """

    dependencies = [
        ("db", "0125_page_node_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="project_markdown_path",
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
        migrations.AddField(
            model_name="workspace",
            name="wiki_markdown_path",
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
    ]
