# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.db import migrations, models


class Migration(migrations.Migration):
    """给 `Page` 加 `node_type`（罗盘 Round D）。

    手写而不是 `makemigrations` 生成：生成的迁移会把 `db/models/page.py` 里那份
    `NODE_TYPE_CHOICES` **展开成字面量**（迁移从不 import 模型常量），两者逐字等价，
    但手写版让人一眼看得见**落进库的就是 `"doc"` / `"folder"` 两根字符串**。

    **additive、有默认值** ⇒ 对存量行安全（Django 直接把它填进 `ADD COLUMN ... DEFAULT`），
    不需要用户确认（CLAUDE.md：新增列不需要确认）。

    依赖必须写 **`0124_pagecollection_external_id_and_more`** —— 那是 `ls` 出来的
    真实 head 名（不是设计文档里简称的 `0124_pagecollection_external_ids`）。
    """

    dependencies = [
        ("db", "0124_pagecollection_external_id_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="page",
            name="node_type",
            field=models.CharField(
                choices=[("doc", "Document"), ("folder", "Folder")],
                default="doc",
                max_length=16,
            ),
        ),
    ]
