# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""`get_markdown_root` / `get_wiki_markdown_root` 的三档优先级。

优先级是 **workspace 字段 > 环境变量 > 内置默认**。UI 必须能覆盖环境变量，
否则「我在界面里改了却没生效」是更坏的意外。
"""

import types
from pathlib import Path

import pytest

from plane.utils.markdown_storage import (
    DEFAULT_PROJECT_MARKDOWN_PATH,
    DEFAULT_WIKI_MARKDOWN_PATH,
    MARKDOWN_STORAGE_PATH_ENV,
    WIKI_MARKDOWN_STORAGE_PATH_ENV,
    get_markdown_root,
    get_wiki_markdown_root,
)

BOTH = (
    (get_markdown_root, MARKDOWN_STORAGE_PATH_ENV, "project_markdown_path", DEFAULT_PROJECT_MARKDOWN_PATH, "~/projects"),
    (get_wiki_markdown_root, WIKI_MARKDOWN_STORAGE_PATH_ENV, "wiki_markdown_path", DEFAULT_WIKI_MARKDOWN_PATH, "~/wiki"),
)


def _workspace(**fields):
    """一个只有属性的假 workspace —— 解析函数是 duck-typed 的（`getattr`）。"""
    return types.SimpleNamespace(**fields)


@pytest.mark.unit
class TestRootPrecedence:
    @pytest.mark.parametrize("getter,env_name,field_name,default,default_text", BOTH)
    def test_workspace_field_beats_env(self, getter, env_name, field_name, default, default_text, monkeypatch, tmp_path):
        monkeypatch.setenv(env_name, str(tmp_path / "from-env"))
        ws = _workspace(**{field_name: str(tmp_path / "from-workspace")})
        assert getter(ws) == tmp_path / "from-workspace"

    @pytest.mark.parametrize("getter,env_name,field_name,default,default_text", BOTH)
    def test_env_beats_default(self, getter, env_name, field_name, default, default_text, monkeypatch, tmp_path):
        monkeypatch.setenv(env_name, str(tmp_path / "from-env"))
        assert getter(_workspace(**{field_name: None})) == tmp_path / "from-env"

    @pytest.mark.parametrize("getter,env_name,field_name,default,default_text", BOTH)
    def test_empty_field_string_falls_through(self, getter, env_name, field_name, default, default_text, monkeypatch, tmp_path):
        """空串与 NULL 同义 —— 前端清空输入框后存下来的是 ""，不是 None。"""
        monkeypatch.setenv(env_name, str(tmp_path / "from-env"))
        assert getter(_workspace(**{field_name: ""})) == tmp_path / "from-env"

    @pytest.mark.parametrize("getter,env_name,field_name,default,default_text", BOTH)
    def test_default_text_is_the_builtin(self, getter, env_name, field_name, default, default_text):
        assert default == default_text

    @pytest.mark.parametrize("getter,env_name,field_name,default,default_text", BOTH)
    def test_identical_defaults_are_distinct_strings(self, getter, env_name, field_name, default, default_text, monkeypatch):
        """删掉 wiki 的兄弟目录推导之后，wiki 根不再跟着 projects 根走。

        两档都清空时，两个根必须落在**各自**的内置默认上，且不再有
        `get_markdown_root().parent` 这层关系。
        """
        from plane.utils.markdown_storage import get_markdown_root as projects, get_wiki_markdown_root as wiki

        monkeypatch.delenv(MARKDOWN_STORAGE_PATH_ENV, raising=False)
        monkeypatch.delenv(WIKI_MARKDOWN_STORAGE_PATH_ENV, raising=False)
        assert projects() == Path("~/projects").expanduser()
        assert wiki() == Path("~/wiki").expanduser()
        assert wiki() != projects().parent / "3-Wiki"

    @pytest.mark.parametrize("getter,env_name,field_name,default,default_text", BOTH)
    def test_tilde_is_expanded(self, getter, env_name, field_name, default, default_text, monkeypatch, tmp_path):
        ws = _workspace(**{field_name: "~/wiki"})
        assert not str(getter(ws)).startswith("~")

    @pytest.mark.parametrize("getter,env_name,field_name,default,default_text", BOTH)
    def test_none_workspace_and_missing_attribute_do_not_raise(self, getter, env_name, field_name, default, default_text, monkeypatch, tmp_path):
        """管理命令与单测里没有请求上下文 —— 传 `None` 或缺属性都必须安全。"""
        monkeypatch.setenv(env_name, str(tmp_path / "from-env"))
        assert getter(None) == tmp_path / "from-env"
        assert getter(object()) == tmp_path / "from-env"
        assert getter(_workspace()) == tmp_path / "from-env"
