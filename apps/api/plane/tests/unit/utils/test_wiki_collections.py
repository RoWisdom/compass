# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.utils.wiki_collections import (
    ARCHIVED,
    GENERAL,
    PREDEFINED_KEYS,
    PRIVATE,
    SHARED,
    partition_pages,
    resolve_collection_key,
)

COLLECTION_A = "11111111-1111-1111-1111-111111111111"
COLLECTION_B = "22222222-2222-2222-2222-222222222222"


class _Page:
    """页面替身：分区函数只读这三个属性，不需要真的 Page 模型。"""

    def __init__(self, *, archived_at=None, access=0, collection_id=None, name=""):
        self.archived_at = archived_at
        self.access = access
        self.collection_id = collection_id
        self.name = name


@pytest.mark.unit
class TestResolveCollectionKey:
    def test_plain_public_page_falls_into_general(self):
        assert resolve_collection_key(archived_at=None, access=0, collection_id=None) == GENERAL

    def test_private_page(self):
        assert resolve_collection_key(archived_at=None, access=1, collection_id=None) == PRIVATE

    def test_page_in_a_user_collection(self):
        assert resolve_collection_key(archived_at=None, access=0, collection_id=COLLECTION_A) == COLLECTION_A

    def test_archived_page(self):
        assert resolve_collection_key(archived_at="2026-01-01", access=0, collection_id=None) == ARCHIVED

    def test_archived_beats_everything(self):
        """归档优先于私有，也优先于用户集合归属。"""
        assert resolve_collection_key(archived_at="2026-01-01", access=1, collection_id=COLLECTION_A) == ARCHIVED

    def test_private_beats_user_collection(self):
        """私有的公开性是硬约束，压过集合归属 —— 否则私有页面会从 Private 分区消失。"""
        assert resolve_collection_key(archived_at=None, access=1, collection_id=COLLECTION_A) == PRIVATE

    def test_shared_is_never_returned(self):
        """OSS 没有"发布"字段，Shared 分区 Phase 1 恒空。见设计文档 §3.3。"""
        combos = [
            dict(archived_at=None, access=0, collection_id=None),
            dict(archived_at=None, access=1, collection_id=None),
            dict(archived_at="2026-01-01", access=0, collection_id=None),
            dict(archived_at=None, access=0, collection_id=COLLECTION_A),
        ]
        for kwargs in combos:
            assert resolve_collection_key(**kwargs) != SHARED

    def test_collection_key_is_a_string_not_a_uuid(self):
        import uuid

        key = resolve_collection_key(archived_at=None, access=0, collection_id=uuid.UUID(COLLECTION_A))
        assert isinstance(key, str)
        assert key == COLLECTION_A


@pytest.mark.unit
class TestPartitionPages:
    def test_empty_input_yields_all_predefined_keys(self):
        result = partition_pages([])
        assert set(PREDEFINED_KEYS) <= set(result.keys())
        assert all(result[key] == [] for key in PREDEFINED_KEYS)

    def test_groups_pages_by_key(self):
        general_page = _Page(name="g")
        private_page = _Page(access=1, name="p")
        archived_page = _Page(archived_at="2026-01-01", name="a")
        collected_page = _Page(collection_id=COLLECTION_A, name="c")

        result = partition_pages([general_page, private_page, archived_page, collected_page])

        assert result[GENERAL] == [general_page]
        assert result[PRIVATE] == [private_page]
        assert result[ARCHIVED] == [archived_page]
        assert result[COLLECTION_A] == [collected_page]

    def test_multiple_user_collections_are_separate_keys(self):
        a = _Page(collection_id=COLLECTION_A)
        b1 = _Page(collection_id=COLLECTION_B)
        b2 = _Page(collection_id=COLLECTION_B)

        result = partition_pages([a, b1, b2])

        assert result[COLLECTION_A] == [a]
        assert result[COLLECTION_B] == [b1, b2]

    def test_preserves_input_order_within_a_key(self):
        first = _Page(name="first")
        second = _Page(name="second")

        result = partition_pages([first, second])

        assert result[GENERAL] == [first, second]

    def test_accepts_a_generator(self):
        pages = (_Page(name=str(i)) for i in range(3))
        result = partition_pages(pages)
        assert len(result[GENERAL]) == 3


@pytest.mark.unit
def test_private_access_constant_matches_the_model():
    """本模块刻意不 import Django，所以 PRIVATE_ACCESS 是手抄的。
    这个测试保证它没跟 Page.PRIVATE_ACCESS 漂移。"""
    from plane.db.models import Page

    from plane.utils.wiki_collections import PRIVATE_ACCESS

    assert PRIVATE_ACCESS == Page.PRIVATE_ACCESS
