# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from pathlib import Path

from plane.utils.agent_run import (
    diff_snapshot,
    new_session_ref,
    permission_mode_for_tier,
    session_dirs,
    skill_body,
    snapshot_tree,
)


def test_snapshot_and_diff_detects_new_and_changed(tmp_path):
    root = tmp_path / "island"
    root.mkdir()
    (root / "a.md").write_text("一", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "b.md").write_text("二", encoding="utf-8")

    before = snapshot_tree(root)
    assert set(before) == {"a.md", "sub/b.md"}

    (root / "sub" / "b.md").write_text("二改了", encoding="utf-8")
    (root / "c.md").write_text("三", encoding="utf-8")

    after = snapshot_tree(root)
    assert diff_snapshot(before, after) == ["c.md", "sub/b.md"]


def test_snapshot_of_missing_root_is_empty(tmp_path):
    assert snapshot_tree(tmp_path / "nope") == {}
    assert diff_snapshot({}, {}) == []


def test_permission_mode_maps_only_readonly_to_read_only():
    assert permission_mode_for_tier("readonly") == "read-only"
    assert permission_mode_for_tier("writer") == "workspace-write"
    assert permission_mode_for_tier("ledger") == "workspace-write"


def test_new_session_ref_wants_exactly_one_new_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    sessions = tmp_path / "sessions" / "--island--"
    sessions.mkdir(parents=True)
    (sessions / "session-aaa").mkdir()
    before = session_dirs()

    (sessions / "session-bbb").mkdir()
    after = session_dirs()
    assert new_session_ref(before, after) == "session-bbb"

    # 一个都没多，或者多了不止一个 ⇒ 空串（宁可空，不要认错）
    assert new_session_ref(after, after) == ""
    (sessions / "session-ccc").mkdir()
    assert new_session_ref(before, session_dirs()) == ""


def test_skill_body_reads_skill_md_or_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    skill = tmp_path / "skills" / "拆解" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: 拆解\n---\n\n先看后写。\n", encoding="utf-8")

    assert "先看后写" in skill_body("拆解")
    assert skill_body("不存在") == ""
