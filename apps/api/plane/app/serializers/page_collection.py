# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import Page, PageCollection

from .base import BaseSerializer
from .page import PageBinaryUpdateSerializer


# 集合名的长度上限。前端同口径的文案是 `wiki_collections.form.name_max_length`，
# 本仓 `common.title_should_be_less_than_255_characters` 也用同一措辞配 255 ——
# 按仓库既有约定读作「最多 255」。见设计 §3.1b 的说明。
MAX_NAME_LENGTH = 255


class PageCollectionSerializer(BaseSerializer):
    """用户自建集合。

    只暴露元信息，不暴露页面列表 —— 页面走 /wiki-pages/ 端点。

    写契约**精确等于** `{name}`：`sort_order` 收成了只读，模型上也没有别的可写字段。
    """

    # 显式声明，而不是靠模型的 `TextField(blank=True)` 派生：那样 DRF 会给出
    # `required=False` + `allow_blank=True`，空名字能一路写进库。
    # **不要**改回 `validate_name`** —— 它挡不住缺键的请求：DRF 只在字段出现在载荷里时
    # 才跑 `validate_<field>`，一个 `{}` 会绕过它建出 `name=""` 的集合，而前端
    # `form.name_required` 要求非空。`allow_blank=False` 让「缺键」「空串」都变 400，
    # 「纯空白」则由 DRF 默认的 `trim_whitespace=True` 先切成 `""` 再被拒。
    name = serializers.CharField(max_length=MAX_NAME_LENGTH, allow_blank=False)

    class Meta:
        model = PageCollection
        fields = ["id", "name", "sort_order", "created_at"]
        # `sort_order` 收成只读：没有任何调用方需要写它，模型有默认值
        # （`Page.DEFAULT_SORT_ORDER` = 65535.0）。收窄之后没有第二个可写字段可以漂。
        read_only_fields = ["id", "sort_order", "created_at"]


class WikiPageSerializer(serializers.ModelSerializer):
    """Wiki 列表里的页面行。

    刻意不复用 PageSerializer：那个类的 label_ids / project_ids 依赖 queryset
    里的 ArrayAgg 注解，直接序列化模型实例会炸；而且列表不需要描述正文。
    """

    collection_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = Page
        fields = [
            "id",
            "name",
            "access",
            "color",
            "parent",
            "is_locked",
            "archived_at",
            "workspace",
            "owned_by",
            "collection_id",
            "logo_props",
            "view_props",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class WikiPageIncludeSerializer(serializers.Serializer):
    """POST /wiki-pages/ 的请求体 —— 把已有页面收录进 Wiki。"""

    page_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    collection_id = serializers.UUIDField(required=False, allow_null=True)


class WikiPageDetailSerializer(WikiPageSerializer):
    """详情页用：在列表字段之上补正文。

    刻意不继承 PageSerializer —— 那个类依赖 queryset 的 ArrayAgg 注解。
    description_binary 也不放进来：它是二进制，走 JSON 序列化没有意义，
    项目页那边是单独一个 octet-stream 端点（PageDescriptionViewSet.retrieve）。
    """

    class Meta(WikiPageSerializer.Meta):
        fields = [*WikiPageSerializer.Meta.fields, "description_html", "description_json"]
        read_only_fields = fields


class WikiPageUpdateSerializer(PageBinaryUpdateSerializer):
    """PATCH wiki-pages/<page_id>/ 的请求体。

    三组字段全部可选、可任意组合：
      - collection_id：换集合；显式传 null 表示移回 general
      - name：改标题（协同服务器的标题同步会 PATCH 它）
      - description_html / description_json / description_binary：正文

    正文的校验与写入直接复用 PageBinaryUpdateSerializer —— HTML 消毒走
    validate_html_content，与项目页是同一条路径，不另起一套。

    collection_id 这里只做语法校验：存在性 / 工作区归属由视图前置查，
    查不到 404 {"error": "Collection not found."} —— 与 create 逐字同形。
    改成在这里 raise ValidationError 会让同一个输入在 POST 上得到 404、
    在 PATCH 上得到 400，两个端点对一个错误的说法不一致。

    description_json 刻意重新声明、去掉基类的 allow_null=True：Page 的
    description_json 列是 jsonb NOT NULL，允许 null 等于放一个必定写库失败
    的值过校验 —— 请求会带着 IntegrityError 撞进 BaseViewSet.handle_exception，
    以一个与字段无关的 400（{"error": "The payload is not valid"}）收场，
    调用方看不出是哪个字段的问题。去掉 allow_null 后 null 在序列化层就被拒
    （400 "This field may not be null."），与兄弟字段 description_html /
    description_binary 的行为一致。基类 PageBinaryUpdateSerializer 上同样的
    声明只留给项目页那条既有路径，不在这里动。
    """

    collection_id = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(required=False, allow_blank=True)
    description_json = serializers.JSONField(required=False)

    def update(self, instance, validated_data):
        collection_provided = "collection_id" in validated_data
        collection_id = validated_data.pop("collection_id", None)

        # Pop `name` before delegating: PageBinaryUpdateSerializer.update() only
        # knows the three description_* fields, so a `name` left in the dict
        # would be silently dropped there — a 200 that changes nothing.
        name_provided = "name" in validated_data
        name = validated_data.pop("name", None)

        # 只给了 collection_id / name 时不要空写一次正文
        if validated_data:
            instance = super().update(instance, validated_data)

        if name_provided:
            instance.name = name

        if collection_provided or name_provided:
            if collection_provided:
                instance.collection_id = collection_id

            # `updated_at` is auto_now, and `_save_table` only runs `pre_save`
            # for the fields named in `update_fields` — leaving it out freezes
            # the timestamp. `-updated_at` is the candidate sort key and the
            # collection/create/destroy paths all refresh it deliberately;
            # renaming a page is a fourth write path and must do the same.
            # `updated_by` is **not** written — same ruling as the
            # include/remove paths: refresh the timestamp, do not stamp the
            # actor onto the "last edited by" slot.
            update_fields = ["updated_at"]
            if name_provided:
                update_fields.append("name")
            if collection_provided:
                update_fields.append("collection")
            instance.save(update_fields=update_fields)

        return instance


class WikiPageCreateSerializer(serializers.Serializer):
    """``POST /wiki-pages/create/`` 的请求体 —— 在 Wiki 里**新建**一个页面。

    与 ``WikiPageIncludeSerializer`` 的分界：那个是「把已有页面收录进来」（吃
    ``page_ids``、回 ``{"included": n}``），这个是「从零建一个页面」。两者都写
    ``is_global``，但只有这里会 create 一行 ``Page``。

    刻意**不继承 ``PageSerializer``**：那个类的 ``create()`` 从项目推 workspace
    （``serializers/page.py:95`` ``workspace_id=project.workspace_id``），而本端点的
    页面**可以没有项目** —— 没有项目就没有 workspace 可推，那一步直接崩。
    workspace 由视图经 ``context`` 直接给，不由任何外键推导。

    ``name`` 允许空、且**不设 max_length**：``Page.name`` 是 ``TextField(blank=True)``，
    i18n 有 ``wiki_collections.list.untitled``（「未命名」）而**没有**「页面名必填」键 ——
    官方把空名页当成一等公民。集合名那个 255 的上限有 ``form.name_max_length`` 文案作
    依据，这里没有对应文案，加限制没有规格依据。
    """

    name = serializers.CharField(required=False, allow_blank=True, default="")
    # ChoiceField 而不是 IntegerField + 手写范围检查：``Page.ACCESS_CHOICES`` 就是
    # 模型自己声明的合法集，写死一遍 (0, 1) 等于在序列化器里存了第二个真相源。
    # 传 7 会被 DRF 拒成 400 并列出合法值，不需要自己拼错误信息。
    access = serializers.ChoiceField(choices=Page.ACCESS_CHOICES, required=False, default=Page.PUBLIC_ACCESS)
    collection_id = serializers.UUIDField(required=False, allow_null=True)

    def create(self, validated_data):
        workspace = self.context["workspace"]
        owned_by = self.context["owned_by"]

        return Page.objects.create(
            name=validated_data.get("name", ""),
            # 建出来就是空正文。``Page.description_html`` 的列默认值也是 ``"<p></p>"``，
            # 这里显式给一次，好让"建的页是什么样"在这一个地方看得全。
            description_html="<p></p>",
            description_json={},
            # 在 Wiki 里建的页面天然已收录 —— 这就是它出现在侧栏的条件。
            is_global=True,
            owned_by=owned_by,
            workspace=workspace,
            access=validated_data.get("access", Page.PUBLIC_ACCESS),
            collection_id=validated_data.get("collection_id"),
        )
