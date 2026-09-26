# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from django.db.models import CASCADE, SET_NULL

from plane.db.models import Page, PageCollection


@pytest.mark.unit
class TestPageCollectionModel:
    """锁死 PageCollection 的设计决策，防止后续被"顺手改掉"。"""

    def test_db_table_name(self):
        assert PageCollection._meta.db_table == "page_collections"

    def test_workspace_fk_cascades(self):
        field = PageCollection._meta.get_field("workspace")
        assert field.remote_field.on_delete is CASCADE
        assert field.remote_field.related_name == "page_collections"

    def test_is_soft_deletable(self):
        # 继承 BaseModel -> AuditModel -> SoftDeleteModel
        assert PageCollection._meta.get_field("deleted_at").null is True

    def test_name_defaults_to_blank(self):
        assert PageCollection._meta.get_field("name").blank is True

    def test_page_collection_field_is_nullable(self):
        field = Page._meta.get_field("collection")
        assert field.null is True
        assert field.blank is True

    def test_deleting_a_collection_does_not_delete_pages(self):
        """刻意用 SET_NULL 而非 CASCADE：页面带着版本历史和评论，
        不该被一次硬删除连坐带走。见设计文档 §3.2。"""
        field = Page._meta.get_field("collection")
        assert field.remote_field.on_delete is SET_NULL
        assert field.remote_field.related_name == "pages"
