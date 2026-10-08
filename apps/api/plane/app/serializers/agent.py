# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third-party imports
from rest_framework import serializers

# Module imports
from plane.db.models import AgentDefinition, AgentGroup, AgentMember, AgentRun


class AgentDefinitionLiteSerializer(serializers.ModelSerializer):
    """岗位的展示面 —— 嵌在成员行里，只够画一行。

    ``instructions`` 与 ``project_count`` 都**不在**这里：名册不需要整篇说明书，
    也不需要知道别的项目怎么用它。
    """

    class Meta:
        model = AgentDefinition
        fields = ["id", "name", "description", "tier", "color"]
        read_only_fields = fields


class AgentDefinitionSerializer(serializers.ModelSerializer):
    """岗位（工作区级）。可写 —— 这是第一处能改说明书的 API。

    ``project_count`` 是只读注解，由 ViewSet 的 queryset 提供（设计 §4.1：
    删除守卫要在动手前就把「被几个项目使用」摆在脸上）。
    """

    project_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AgentDefinition
        fields = [
            "id",
            "workspace_id",
            "name",
            "description",
            "instructions",
            "skills",
            "tier",
            "model",
            "profile",
            "web_access",
            "trusted_urls",
            "writable_paths",
            "color",
            "project_count",
            "created_at",
            "updated_at",
        ]
        # ``workspace_id`` 由视图按 URL 里的 slug 填，不接受客户端指定。
        read_only_fields = ["id", "workspace_id", "project_count", "created_at", "updated_at"]

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("A post needs a name.")
        return value


class AgentGroupLiteSerializer(serializers.ModelSerializer):
    """岗位组的展示面 —— 嵌在成员行里，只够画一个标签。"""

    class Meta:
        model = AgentGroup
        fields = ["id", "name"]
        read_only_fields = fields


class AgentGroupSerializer(serializers.ModelSerializer):
    """岗位组（工作区级）。设计 §1。

    **名册是「读嵌套 + 写 id 列表」两件分开的字段，不是一个可写 M2M。**
    DRF 默认会把 ``definitions`` 生成成可写的 ``PrimaryKeyRelatedField(many=True)``，
    而 ``ModelSerializer.update`` 对它是 ``set()`` —— 见 ``utils/agent_group.py`` 的
    ⚠️：``set()`` 会静默抹掉软删岗位的 through 行，然后那次差集把活着的成员解绑。
    ⇒ 读走嵌套 ``definitions``，写走 ``definition_ids``，增量由视图显式算。

    ``project_count`` 是只读注解，由 ViewSet 的 queryset 提供（删除守卫要在动手前
    就把「被几个项目用着」摆在脸上，与 ``AgentDefinitionSerializer`` 同形）。
    """

    definitions = AgentDefinitionLiteSerializer(many=True, read_only=True)
    definition_ids = serializers.ListField(
        child=serializers.UUIDField(), write_only=True, required=False
    )
    project_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AgentGroup
        fields = [
            "id",
            "workspace_id",
            "name",
            "description",
            "instructions",
            "definitions",
            "definition_ids",
            "project_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "workspace_id", "project_count", "created_at", "updated_at"]

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("A post group needs a name.")
        return value


class AgentMemberSerializer(serializers.ModelSerializer):
    """一个项目里的成员行。

    ``definition`` 是**嵌套只读**；写入走 ``definition_id``（仅创建时）。
    ``last_run_*`` 是只读注解，由 ViewSet 提供（设计 §3：显示**运行状态**，
    不造可用性点）。
    """

    definition = AgentDefinitionLiteSerializer(read_only=True)
    definition_id = serializers.UUIDField(write_only=True, required=False)
    #: 第三期：这个成员是从哪个岗位组部署来的。**只读** —— 换组只能靠重新部署那个组，
    #: 就像换岗位只能「移除后重加」（设计 §2）。名册要显示得出来，好让「为什么这个成员
    #: 的正文和隔壁不一样」一眼有答案。
    group = AgentGroupLiteSerializer(read_only=True)
    last_run_status = serializers.CharField(read_only=True, allow_null=True)
    last_run_at = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = AgentMember
        fields = [
            "id",
            "project_id",
            "definition",
            "definition_id",
            "group",
            "is_active",
            "last_run_status",
            "last_run_at",
            "created_at",
        ]
        read_only_fields = ["id", "project_id", "group", "created_at"]


class AgentRunSerializer(serializers.ModelSerializer):
    """Read-only view of a run. Creation goes through ``AgentRunViewSet.create``."""

    class Meta:
        model = AgentRun
        fields = [
            "id",
            "member_id",
            "issue_id",
            "trigger",
            "status",
            "session_ref",
            "artifacts",
            "exit_code",
            "error",
            "started_at",
            "finished_at",
            "created_at",
        ]
        read_only_fields = fields
