# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

r"""identifier 里「``_sanitize_name`` 会改写」的字符必须被拒 —— 否则两个项目共用一个
沙箱岛。（F1：本轮终审的 Important。）

``_sanitize_name``（``plane/utils/markdown_storage.py``）把 ``[\\/:*?"<>|\x00-\x1f]``
替换成 ``-``，并把空白串折成一个空格。而 identifier 的**旧**校验只挡了 ``: * ? < > |``
—— 于是 ``A/B``、``A\B``、``A"B``、``A B``（含**内部空格**）全都是**合法** identifier，
却都折进同一个目录 ⇒ 两个项目共用同一个沙箱岛。

``Project.FORBIDDEN_IDENTIFIER_PATH_CHARS_PATTERN`` 把这批字符收进 identifier 的禁用集。
它**只**用于 identifier：project **name** 那几道校验逐字未动（名字不进路径，岛按
identifier 命名），本文件用一条回归锁把这点钉住 —— 收紧名字校验是本轮**不要**的副作用。

两个 serializer 家族的入口不同，都要覆盖：
  · ``plane.app.serializers.project.ProjectSerializer`` → ``validate_identifier``
    （构造时须 ``context={"workspace_id": ...}``）；
  · ``plane.api.serializers.project.ProjectCreateSerializer`` → ``validate``
    （``data`` 里要带 ``name`` 与 ``identifier``）。
"""

import pytest
from rest_framework import serializers

from plane.api.serializers.project import ProjectCreateSerializer
from plane.app.serializers.project import ProjectSerializer

pytestmark = pytest.mark.django_db

# 必须被拒。前四条是 F1 收口的（净化后撞进**另一个**合法 identifier 的目录）；
# 最后一条 ``A:B`` 是**回归**：旧规则本就挡 ``:``。
REJECTED_IDENTIFIERS = [
    "A/B",  # _sanitize_name → "A-B"，与下面那条撞
    "A\\B",  # Python 字面量 = A + 一个反斜杠 + B；同样 → "A-B"
    'A"B',  # _sanitize_name → "A-B"
    "A B",  # 内部空格：与 "A  B"（两空格）折成同一个 "A B"
    "A:B",  # 旧规则 FORBIDDEN_IDENTIFIER_CHARS_PATTERN 本就挡（回归）
]

# 必须被接受 —— 守住这次收口没有把正常值一并拒掉。
ACCEPTED_IDENTIFIERS = [
    "FABRIC",
    "AB_12",
]


@pytest.mark.unit
class TestAppProjectSerializerIdentifierGuard:
    """``plane.app`` 家族的入口：``validate_identifier``（文件级校验）。"""

    def _serializer(self, workspace):
        return ProjectSerializer(context={"workspace_id": str(workspace.id)})

    @pytest.mark.parametrize("identifier", REJECTED_IDENTIFIERS)
    def test_rejects_identifiers_that_sanitize_onto_another(self, workspace, identifier):
        """每个必须被拒的 identifier 都抛 ``PROJECT_IDENTIFIER_CANNOT_CONTAIN_SPECIAL_CHARACTERS``。"""
        with pytest.raises(serializers.ValidationError) as exc_info:
            self._serializer(workspace).validate_identifier(identifier)

        assert "PROJECT_IDENTIFIER_CANNOT_CONTAIN_SPECIAL_CHARACTERS" in str(exc_info.value)

    @pytest.mark.parametrize("identifier", ACCEPTED_IDENTIFIERS)
    def test_accepts_legal_identifiers(self, workspace, identifier):
        """合法 identifier 原样通过（校验器返回它自己）。"""
        assert self._serializer(workspace).validate_identifier(identifier) == identifier


@pytest.mark.unit
class TestApiProjectCreateSerializerIdentifierGuard:
    """``plane.api`` 家族的入口：``validate``（``data`` 里带 ``name`` + ``identifier``）。"""

    def _validate(self, workspace, identifier, name="Island Test Project"):
        serializer = ProjectCreateSerializer(context={"workspace_id": str(workspace.id)})
        return serializer.validate({"name": name, "identifier": identifier})

    @pytest.mark.parametrize("identifier", REJECTED_IDENTIFIERS)
    def test_rejects_identifiers_that_sanitize_onto_another(self, workspace, identifier):
        """每个必须被拒的 identifier 都抛 ``ValidationError``。

        这里**不**钉死错误文本：``A:B`` 命中的是**旧**那道（prose 文案），F1 新挡的那批
        命中的是 ``PROJECT_IDENTIFIER_CANNOT_CONTAIN_SPECIAL_CHARACTERS`` 码。两者都
        是「被拒」，而本测试要锁的正是「被拒」。
        """
        with pytest.raises(serializers.ValidationError):
            self._validate(workspace, identifier)

    @pytest.mark.parametrize("identifier", ACCEPTED_IDENTIFIERS)
    def test_accepts_legal_identifiers(self, workspace, identifier):
        """合法 identifier 通过，且原样留在 data 里。"""
        data = self._validate(workspace, identifier)
        assert data["identifier"] == identifier


@pytest.mark.unit
class TestNameValidationIsUntouched:
    """F1 只收 identifier：project **name** 含 ``/`` 仍被**接受**。

    ``/`` 不进 ``FORBIDDEN_IDENTIFIER_CHARS_PATTERN``，也不该被新常量波及 —— 名字不进
    路径（岛按 identifier 命名），收紧名字校验是本轮不要的副作用。两个家族各锁一次。
    """

    NAME_WITH_SLASH = "研发/测试"

    def test_app_serializer_still_accepts_a_slash_in_the_name(self, workspace):
        """``validate_name`` 原样返回含 ``/`` 的名字。"""
        serializer = ProjectSerializer(context={"workspace_id": str(workspace.id)})
        assert serializer.validate_name(self.NAME_WITH_SLASH) == self.NAME_WITH_SLASH

    def test_api_serializer_still_accepts_a_slash_in_the_name(self, workspace):
        """``ProjectCreateSerializer.validate`` 原样放行含 ``/`` 的名字。"""
        serializer = ProjectCreateSerializer(context={"workspace_id": str(workspace.id)})
        data = serializer.validate({"name": self.NAME_WITH_SLASH, "identifier": "SLSH"})
        assert data["name"] == self.NAME_WITH_SLASH
