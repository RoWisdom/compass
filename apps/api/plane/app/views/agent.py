# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import IntegrityError, transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone

# Third-party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import (
    AgentDefinitionSerializer,
    AgentGroupSerializer,
    AgentMemberSerializer,
    AgentRunSerializer,
)
from plane.bgtasks.agent_run_task import run_agent_member
from plane.db.models import (
    AGENT_UNFINISHED_STATUSES,
    AgentDefinition,
    AgentGroup,
    AgentMember,
    AgentRun,
    AgentRunStatusEnum,
    Issue,
    Project,
    Workspace,
)
from plane.utils.agent_group import (
    RosterIncomplete,
    apply_roster_delta,
    deploy_group,
    raw_roster_ids,
    unbind_members_of,
)
from plane.utils.agent_identity import deploy, resync_project_roles, retire
from plane.utils.exception_logger import log_exception

from .base import BaseViewSet


class AgentDefinitionViewSet(BaseViewSet):
    """岗位库（工作区级）。设计 §4.1。

    读 `[ADMIN, MEMBER, GUEST]`；**写只有 ADMIN** —— 岗位是共享资产，
    在某一个项目的设置页里改它，会静默改掉另外几个项目里那个成员的行为。
    """

    serializer_class = AgentDefinitionSerializer
    model = AgentDefinition

    def get_queryset(self):
        return (
            AgentDefinition.objects.filter(workspace__slug=self.kwargs.get("slug"))
            .annotate(
                project_count=Count(
                    "members",
                    filter=Q(members__deleted_at__isnull=True),
                    distinct=True,
                )
            )
            .order_by("created_at")
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def retrieve(self, request, slug, pk):
        definition = self.get_queryset().filter(pk=pk).first()
        if definition is None:
            return Response({"error": "No such AI post"}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(definition).data)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug):
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return Response({"error": "No such workspace"}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            # 套一层 atomic ⇒ 失败的 INSERT 退回**保存点**，外层事务仍可用。理由与
            # `partial_update` 那段逐字相同：`AgentDefinition` 无 parent，`Model.save()`
            # 走 ``mark_for_rollback_on_error``，在已有事务里它只置 ``needs_rollback``
            # 而不回退 ⇒ 本支眼下没有后续查询所以不炸，但那是**凑巧**，两个兄弟保持一致。
            with transaction.atomic():
                definition = serializer.save(workspace=workspace, created_by_id=request.user.id)
        except IntegrityError:
            # 同一工作区里岗位名唯一（0129 的 unique_agent_definition_name_per_workspace）。
            # **别指望序列化器替你挡** —— DRF 确实会为这条条件唯一约束造一个
            # UniqueTogetherValidator，但因为 ``workspace_id`` 是只读字段、又没有默认值，
            # DRF **会跳过**那条校验（实测 DRF 3.17.1）⇒ 重名会一路撞到 INSERT，
            # 不打这个 except 就是 **500 + 栈**，而不是 400。唯一约束才是仲裁者。
            name = serializer.validated_data.get("name")
            return Response(
                {"error": f"这个工作区里已经有叫「{name}」的岗位了。"},
                status=status.HTTP_409_CONFLICT,
            )
        # 走一遍**带注解的** queryset：``project_count`` 是注解，刚建的对象上没有这个属性，
        # 直接序列化会抛 AttributeError。成员那边的 create 出于同样理由也这么做。
        return Response(
            self.get_serializer(self.get_queryset().filter(pk=definition.pk).first()).data,
            status=status.HTTP_201_CREATED,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, pk):
        definition = self.get_queryset().filter(pk=pk).first()
        if definition is None:
            return Response({"error": "No such AI post"}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(definition, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        # 改档位前先记下旧值。沙箱模式（``AgentMember.permission_mode``）是**现场**从
        # ``definition.tier`` 推的，但 bot 的公开 API 权限是部署时**快照**下来的
        # ``ProjectMember.role``（``agent_identity.py``）—— 只改前者会让两者静默分叉。
        old_tier = definition.tier
        try:
            # 套一层 atomic ⇒ 失败的 UPDATE 退回**保存点**，外层事务仍可用。
            # 不套的话 `Model.save()` 走的是 `mark_for_rollback_on_error`，在已有事务里它只把
            # `needs_rollback` 置真、不回退任何东西 ⇒ 同一请求/测试里再查一次库就是
            # `TransactionManagementError`（实测：改名失败后读原名的那句断言就是这样炸的）。
            with transaction.atomic():
                serializer.save()
                # 档位**真的变了**才重算 bot 的项目角色，且必须与这次保存同在一个 atomic 里 ——
                # 档位改了、角色没跟上，就是半成品状态。不重算的话（终审 I-1）：
                # ``ledger → readonly`` 沙箱变只读、不再挪卡，但 bot 仍留着 MEMBER 级的触达
                # （「可见的按钮不是唯一的栅栏」）；``readonly → ledger`` 则被
                # ``ProjectEntityPermission`` 挡掉 ⇒ 档位静默不生效。
                if "tier" in serializer.validated_data and definition.tier != old_tier:
                    # 重算规则**走那条委托链**，且只在**一个**地方实现：
                    # ``agent_identity.resync_project_roles`` 是「档位 → 角色」这条链的
                    # **唯一出口**（``retire()`` 是它更新形状的先例）。视图、管理命令
                    # ``seed_agent_members`` 都调它 —— 同一条规则存两份就是多一份。
                    resync_project_roles(definition)
        except IntegrityError:
            # 改名撞上另一个岗位（同 create 的理由：DRF 跳过了那条 UniqueTogetherValidator）。
            name = serializer.validated_data.get("name") or definition.name
            return Response(
                {"error": f"这个工作区里已经有叫「{name}」的岗位了。"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(self.get_serializer(definition).data)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, pk):
        """被引用就拒绝，并把挡路的项目名报出来。设计 §6③。

        本期**不做级联**：级联要跨项目删 bot 用户、废 token、摘 ProjectMember，
        是重破坏。守卫 + 报出阻挡者就够了。

        ⚠️ **这道守卫是唯一的栅栏。** 不要以为 `AgentMember.definition` 上的
        `on_delete=PROTECT` 兜得住 —— 软删路径**走不到 PROTECT**：
          * ``SoftDeleteModel.delete()`` 默认 `soft=True`，只 `save(deleted_at=now)`
            + 发一个 Celery 任务，**不进 Django 的 FK collector** ⇒ `ProtectedError`
            永不触发；
          * 那个任务（``soft_delete_related_objects``，``bgtasks/deletion_task.py``）
            把反向关系里**除了 DO_NOTHING / SET_NULL 之外的一切**都当 CASCADE
            软删，**PROTECT 也落进那个分支**。
        所以一旦用实例的 ``.delete()``，这个岗位在**所有项目**的成员行会被一起
        软删 —— 正是本节说不做的那种跨项目破坏，且绕过了守卫。
        ⇒ 必须用下面的 **queryset 形式**（只 `update(deleted_at=now)`，不发级联任务）。
        """
        definition = self.get_queryset().filter(pk=pk).first()
        if definition is None:
            return Response({"error": "No such AI post"}, status=status.HTTP_404_NOT_FOUND)

        blockers = (
            AgentMember.objects.filter(definition=definition, deleted_at__isnull=True)
            .select_related("project")
        )
        # 计数按 **``project_id`` 去重**，不按项目名（终审 M-2）。项目名只对**未删除**的项目
        # 唯一（``project_unique_name_workspace_when_deleted_at_null``）—— 软删过的项目可以
        # 和一个活跃项目同名，而它底下的成员行不会随之消失（本查询只按成员行自己的
        # ``deleted_at`` 过滤）。两个这样的项目都用同一个岗位时，按名字去重会把「2」报成「1」。
        # 这个数字是给用户看的**事实陈述**，必须等于**不同项目的个数**；展示用的名字列表另列，
        # 项目真同名就重复出现，不许为了好看让列表和数字打架。``(project, definition)`` 的唯一
        # 约束保证成员行与不同项目一一对应，所以 ``names`` 与 ``project_count`` 长度相等。
        names = sorted(m.project.name for m in blockers)
        project_count = len({m.project_id for m in blockers})
        if project_count:
            listed = "、".join(names)
            return Response(
                {
                    "error": (
                        f"「{definition.name}」仍被 {project_count} 个项目使用：{listed}。"
                        "先从那些项目移除。"
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        # 第三期：还在某个**活着的**岗位组名册里，也拒绝删 —— buzz 的 ``deletePersona``
        # 同样拒绝 team-referenced 的 persona。要删就先把它从那些组的名册里摘掉。
        # 注意 ``definition.groups`` 是**反向 M2M ⇒ 过滤软删的组**（钉死在
        # tests/unit/models/test_agent_group.py），所以「组已经删了但 through 行还在」
        # 不挡删除 —— 那正是这里想要的行为。
        group_names = sorted({group.name for group in definition.groups.all()})
        if group_names:
            listed = "、".join(group_names)
            return Response(
                {
                    "error": (
                        f"「{definition.name}」还在 {len(group_names)} 个岗位组的名册里：{listed}。"
                        "先从那些组里摘出来。"
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        # **queryset 形式，不是 definition.delete()** —— 理由见上面 docstring 的 ⚠️。
        # AgentMember 那边的 destroy 出于同样的理由也走 queryset 形式。
        AgentDefinition.objects.filter(pk=definition.pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _resolve_roster(slug, definition_ids):
    """把请求里的岗位 id 列表收窄到**本工作区、还活着**的岗位。

    返回 ``(ids, error)``：``ids`` 是 ``None`` 表示「请求里没提名册」（与「名册清空」
    是两件不同的事，见 ``partial_update``），否则是一个 set；``error`` 非 ``None``
    时调用方原样当 400 返回。

    两道收窄都必要。跨工作区的 UUID 进得来（``ListField(child=UUIDField())`` 不做
    任何归属校验），而名册是**工作区级**资产 —— 放一个别的工作区的岗位进来，等于
    给它开了一条**静默生效的正文通道**：部署时 ``_deploy`` 照样按 ``definition``
    铸 bot 身份，不看它在哪个工作区（连 ``workspace=project.workspace`` 都是现取的）。
    软删的岗位同理：它已经不该出现在任何名册里，让 ``add()`` 写进一条 through 行
    只会让下一次部署撞上 ``RosterIncomplete``。
    """
    if definition_ids is None:
        return None, None
    # 去重保序：客户端勾选框重复提交同一个 id 是常事，不该变成两条 through 行。
    wanted = list(dict.fromkeys(definition_ids))
    found = set(
        AgentDefinition.objects.filter(
            workspace__slug=slug, pk__in=wanted, deleted_at__isnull=True
        ).values_list("id", flat=True)
    )
    missing = [str(pk) for pk in wanted if pk not in found]
    if missing:
        return None, (
            f"名册里有 {len(missing)} 个岗位不属于这个工作区，或者已经删掉了："
            f"{'、'.join(missing)}"
        )
    return found, None


class AgentGroupViewSet(BaseViewSet):
    """岗位组（工作区级）。第三期设计 §1 / §4 / §5。

    读 `[ADMIN, MEMBER, GUEST]`；写只有 ADMIN —— 与岗位库同一条理由，只是更重：
    改一个组的正文会**同时**改掉它名下**所有项目**里所有成员的运行说明。

    ⚠️ **这一类全部方法都必须 ``level="WORKSPACE"``**（只有项目内的 ``deploy`` 例外）。
    ``allow_permission`` 的 ``level`` 默认是 ``"PROJECT"``，而它会**无条件**读
    ``kwargs["project_id"]``（``app/permissions/base.py:56``）—— 组 CRUD 的 URL 里
    根本没有项目 ⇒ 照抄 ``AgentMemberViewSet`` 的装饰器会当场 ``KeyError`` ⇒ 500。
    """

    serializer_class = AgentGroupSerializer
    model = AgentGroup

    def get_queryset(self):
        """本工作区的组，各带「被几个项目用着」。

        ``prefetch_related("definitions")`` 不是为了省查询这么简单：列表序列化器把
        名册**嵌在每一行里**（卡片要按岗位叠色点），不快取就是每组一次查询。
        """
        return (
            AgentGroup.objects.filter(workspace__slug=self.kwargs.get("slug"))
            .prefetch_related("definitions")
            .annotate(
                # 数的是**不同的项目**，不是成员行 —— 与岗位那边不同，同一个组在同一个
                # 项目里可以有好几个成员（``(project, definition)`` 唯一，组不唯一），
                # 按成员行去重会把「1 个项目」报成「3」。成员是**反向 FK ⇒ 过滤软删**，
                # 正是这里要的（这个数字是删组守卫的事实陈述，只该数还活着的成员）。
                project_count=Count(
                    "members__project_id",
                    filter=Q(members__deleted_at__isnull=True),
                    distinct=True,
                )
            )
            .order_by("created_at")
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def retrieve(self, request, slug, pk):
        group = self.get_queryset().filter(pk=pk).first()
        if group is None:
            return Response({"error": "No such AI group"}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(group).data)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug):
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return Response({"error": "No such workspace"}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        # ⚠️ **必须在 ``save()`` 之前把 ``definition_ids`` 从 ``validated_data`` 里摘掉。**
        # 它不是模型字段，而 ``ModelSerializer.create`` 会把剩下的一切原样喂给
        # ``AgentGroup.objects.create(**validated_data)`` ⇒ 不摘就是
        # ``TypeError: 'definition_ids' is an invalid keyword argument``。名册由下面
        # 显式算增量（见 ``utils/agent_group.py`` 的 ⚠️：绝不能用 M2M 的 ``set()``）。
        definition_ids = serializer.validated_data.pop("definition_ids", None)
        roster, error = _resolve_roster(slug, definition_ids)
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
        try:
            # 套一层 atomic 的理由与 ``AgentDefinitionViewSet.create`` 逐字相同：
            # 失败的 INSERT 退回保存点，外层事务仍可用。
            with transaction.atomic():
                group = serializer.save(workspace=workspace, created_by_id=request.user.id)
        except IntegrityError:
            # 同工作区里组名唯一（0130 的 unique_agent_group_name_per_workspace）。
            # 与岗位那边同一个陷阱：DRF 造了 ``UniqueTogetherValidator``，但
            # ``workspace_id`` 只读又无默认值 ⇒ **它会被整条跳过** ⇒ 重名一路撞到
            # INSERT。不打这个 except 就是 500 + 栈，而不是 409。
            name = serializer.validated_data.get("name")
            return Response(
                {"error": f"这个工作区里已经有叫「{name}」的岗位组了。"},
                status=status.HTTP_409_CONFLICT,
            )
        if roster:
            with transaction.atomic():
                # 新建的组旧名册必然是空集 —— 但没有「必然」可言：这条断言靠的是
                # 「刚 INSERT 的组不可能有 through 行」。直说，别让它变成一句心算。
                apply_roster_delta(group=group, previous_ids=set(), definition_ids=roster)
        # 走一遍**带注解的** queryset：``project_count`` 是注解，刚建的对象上没有这个
        # 属性，直接序列化会抛 AttributeError（岗位/成员那边的 create 同此）。
        return Response(
            self.get_serializer(self.get_queryset().filter(pk=group.pk).first()).data,
            status=status.HTTP_201_CREATED,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, pk):
        """改组。名册的增删会**扇出到成员行**（设计 §4）—— 这是唯一一处写组会顺带改成员。"""
        group = self.get_queryset().filter(pk=pk).first()
        if group is None:
            return Response({"error": "No such AI group"}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(group, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        definition_ids = serializer.validated_data.pop("definition_ids", None)
        roster, error = _resolve_roster(slug, definition_ids)
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
        # **「没提名册」与「清空名册」是两件事**：只改名字的 PATCH 不该顺手把名册清掉。
        # ``definition_ids: []`` 才是「清空」；字段缺席时 ``roster`` 是 ``None``。
        # 还有一件事同样致命：**旧名册必须在 ``save()`` 之前抓**，且抓的是
        # ``raw_roster_ids``（全部 through 行，**含软删岗位**）。先 save 再读 ⇒
        # ``added``/``removed`` 双双为空 ⇒ 整段静默变成空转，而且没有任何测试会红。
        previous_ids = raw_roster_ids(group) if roster is not None else None
        try:
            with transaction.atomic():
                serializer.save()
                if roster is not None:
                    apply_roster_delta(
                        group=group, previous_ids=previous_ids, definition_ids=roster
                    )
        except IntegrityError:
            name = serializer.validated_data.get("name") or group.name
            return Response(
                {"error": f"这个工作区里已经有叫「{name}」的岗位组了。"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(self.get_serializer(self.get_queryset().filter(pk=pk).first()).data)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, pk):
        """还绑着成员就拒绝删，并把挡路的项目名报出来。设计 §5。

        **这是拒绝式，不是级联式** —— 照 buzz 的 ``delete_team_with_cascade``：只要
        还有实例引用这个组，就删不掉（那句「已部署的 agent 不受影响」是**拒绝的结果**，
        不是删除的行为）。罗盘的修法与 buzz 同一句话：先把成员从组里解绑。

        ⚠️ 与岗位那边一样，**这道守卫是唯一的栅栏**：``AgentMember.group`` 上的
        ``on_delete=PROTECT`` 在软删路径上**永远不触发**（软删不进 FK collector），
        而实例的 ``.delete()`` 会发级联任务、把反向关系里连 PROTECT 都当 CASCADE。
        所以下面必须是 **queryset 形式**。
        """
        group = self.get_queryset().filter(pk=pk).first()
        if group is None:
            return Response({"error": "No such AI group"}, status=status.HTTP_404_NOT_FOUND)

        # 与 ``AgentDefinitionViewSet.destroy`` 不同：那边的 ``(project, definition)``
        # 唯一约束保证成员行与项目一一对应，所以名字列表可以直接由成员行生成。这里一条
        # 项目可以贡献好几个成员（同一组里好几个岗位部署进同一个项目）⇒ 数字和名单
        # **都必须按 ``project_id`` 去重**，否则会输出「仍被 1 个项目使用：P、P、P」。
        blockers = AgentMember.objects.filter(group=group, deleted_at__isnull=True).select_related(
            "project"
        )
        project_names = sorted({m.project.name for m in blockers})
        project_count = len({m.project_id for m in blockers})
        if project_count:
            return Response(
                {
                    "error": (
                        f"「{group.name}」还绑着 {project_count} 个项目里的成员："
                        f"{'、'.join(project_names)}。先把那些成员从组里解绑，再删这个组。"
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            # 守卫保证这里匹配零行；调用它是为了让「组没了、绑定还指着它」这个中间态
            # **不可表示** —— 万一哪天守卫被绕过，也不会有成员的 ``group_id`` 指向坟头。
            unbind_members_of(group)
            AgentGroup.objects.filter(pk=group.pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @allow_permission([ROLE.ADMIN])
    def deploy(self, request, slug, project_id, pk):
        """把整组岗位一次部署进这个项目。设计 §3。

        ⚠️ 装饰器**没有** ``level`` ⇒ 走默认的 ``"PROJECT"``，而它会去读
        ``kwargs["project_id"]`` ⇒ 路由里的 kwarg **必须**字面叫 ``project_id``
        （``<uuid:project_id>``）。写成 ``<uuid:pid>`` 就是 ``KeyError`` ⇒ 500。

        组的取法沿本类的 ``get_queryset()`` ⇒ 自动按 ``workspace__slug`` 收窄。
        少了这一步，A 工作区的项目管理员能部署 B 工作区的组，而 ``_deploy`` 照样铸
        身份（它的 ``workspace`` 是现从 ``project`` 上取的，不看组属于谁）。
        """
        group = self.get_queryset().filter(pk=pk).first()
        if group is None:
            return Response({"error": "No such AI group"}, status=status.HTTP_404_NOT_FOUND)
        project = Project.objects.filter(workspace__slug=slug, pk=project_id).first()
        if project is None:
            return Response({"error": "No such project"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # 外层 atomic：逐项 ``deploy()`` 自带事务（嵌套时退化为保存点），所以里面
            # 撞唯一约束的那一支回退到保存点、循环能继续 ⇒ **部分成功仍然成立**。
            # 这一层买的是「意外异常 ⇒ 整组回滚」，不留半个组。
            with transaction.atomic():
                result = deploy_group(group=group, project=project, created_by_id=request.user.id)
        except RosterIncomplete as exc:
            # 名册里有已删的岗位 = buzz 的「部署按钮禁用 + 红条」。**编辑是修复路径**：
            # 所以这里只报错、不替用户把死岗位摘掉 —— 摘掉是不可逆的静默编辑。
            listed = "、".join(sorted(exc.names))
            return Response(
                {
                    "error": (
                        f"这个岗位组的名册里有 {len(exc.names)} 个岗位已经删掉了：{listed}。"
                        "先改组，把它们从名册里摘出来。"
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        # 四个格子都报回去（``utils/agent_group.py`` 的 ``deploy_group``）。**部分成功是
        # 一等公民** —— 前端照 buzz 报「部署 N 个，M 个冲突」，不是一句「失败」。
        result["group"] = {"id": str(group.id), "name": group.name}
        return Response(result, status=status.HTTP_200_OK)


class AgentMemberViewSet(BaseViewSet):
    serializer_class = AgentMemberSerializer
    model = AgentMember

    def get_queryset(self):
        """本项目的成员行，**含停用的**，各带最近一次运行。

        ``last_run_*`` 用 Subquery 注解，一行一条 —— 名册要显示「运行状态」，
        但绝不把它当成「在线」。设计 §3。
        """
        latest = (
            # `-created_at` 之后还要有 `-id` 兜底：``last_run_status`` 与 ``last_run_at``
            # 是**两条独立的标量子查询**，只按 created_at 排序时，两个同一时刻的 run
            # 可能分别被两条子查询选中 ⇒ 状态来自 A、时间来自 B。加个确定性次序键，
            # 「两列必来自同一行」这条不变量才真成立。
            AgentRun.objects.filter(member=OuterRef("pk"))
            .order_by("-created_at", "-id")
        )
        return (
            AgentMember.objects.filter(
                workspace__slug=self.kwargs.get("slug"),
                project_id=self.kwargs.get("project_id"),
                deleted_at__isnull=True,
            )
            .select_related("definition")
            .annotate(
                last_run_status=Subquery(latest.values("status")[:1]),
                last_run_at=Subquery(latest.values("created_at")[:1]),
            )
            .order_by("created_at")
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def list(self, request, slug, project_id):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)

    @allow_permission([ROLE.ADMIN])
    def create(self, request, slug, project_id):
        """把一个岗位加进这个项目。设计 §4.2。"""
        definition_id = request.data.get("definition_id")
        definition = AgentDefinition.objects.filter(
            workspace__slug=slug, pk=definition_id, deleted_at__isnull=True
        ).first()
        if definition is None:
            return Response({"error": "No such AI post in this workspace"}, status=status.HTTP_400_BAD_REQUEST)

        project = Project.objects.filter(workspace__slug=slug, pk=project_id).first()
        if project is None:
            return Response({"error": "No such project"}, status=status.HTTP_400_BAD_REQUEST)

        if AgentMember.objects.filter(
            project=project, definition=definition, deleted_at__isnull=True
        ).exists():
            return Response(
                {"error": f"「{definition.name}」已经在这个项目里了。"},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            member = deploy(definition=definition, project=project, created_by_id=request.user.id)
        except IntegrityError:
            # 上面那次 exists() 与本行 INSERT 之间输了竞速 —— 唯一约束是仲裁者。
            return Response(
                {"error": f"「{definition.name}」已经在这个项目里了。"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            self.get_serializer(self.get_queryset().filter(pk=member.pk).first()).data,
            status=status.HTTP_201_CREATED,
        )

    @allow_permission([ROLE.ADMIN])
    def partial_update(self, request, slug, project_id, pk):
        """**只有 `is_active` 可改。** 说明书不是这一行的字段（设计 §2）。

        ⚠️ **必须显式拒绝 `definition_id`，不能指望序列化器。** DRF 的 ``update()``
        会对 ``validated_data`` 里**每一个**键 ``setattr``，而序列化器把
        ``definition_id`` 声明成了可写字段 ⇒ 不拦的话一次
        ``PATCH {"definition_id": …}`` 就能把这个成员**悄悄改挂到另一个岗位**，
        却仍留着旧的 ``bot_user`` / ``service_token``：从此它按**新**岗位的说明书、
        用**旧**岗位的凭证跑。跨工作区的 UUID 也进得来（那个字段没有 queryset 校验）。
        设计 §2 明说只能「移除后重加」。
        """
        member = self.get_queryset().filter(pk=pk).first()
        if member is None:
            return Response({"error": "No such AI member"}, status=status.HTTP_404_NOT_FOUND)
        if "definition_id" in request.data:
            return Response(
                {"error": "岗位不能改。要换岗位，请先把这一行移除，再重新添加。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = self.get_serializer(member, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(self.get_serializer(member).data)

    @allow_permission([ROLE.ADMIN])
    def destroy(self, request, slug, project_id, pk):
        """从项目移除。设计 §6②：软删成员行 + 摘项目身份 + 废 token。"""
        member = self.get_queryset().filter(pk=pk).first()
        if member is None:
            return Response({"error": "No such AI member"}, status=status.HTTP_404_NOT_FOUND)
        retire(member)
        # **queryset delete，不是 ``member.delete()``。** 两者都软删，但实例方法还会
        # `soft_delete_related_objects.delay(...)`（`db/mixins.py:56-90`），而那个任务
        # 沿**反向关系**把该成员名下所有 ``AgentRun`` 一起软删
        # （`bgtasks/deletion_task.py` 遍历 ``_meta.get_fields()`` 的 one_to_many）。
        # 跑过的记录是审计，移除一个成员不该把历史抹掉 —— 与设计 §6「本期不做级联」同向，
        # 也与 buzz 把运行痕迹留在 relay 上的做法同向（buzz 的 relay 事件永不删）。
        AgentMember.objects.filter(pk=member.pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AgentRunViewSet(BaseViewSet):
    serializer_class = AgentRunSerializer
    model = AgentRun

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def list(self, request, slug, project_id):
        """This project's runs, newest first.

        ``BaseViewSet.get_queryset`` is ``self.model.objects.all()`` — unscoped — so
        leaving ``list`` to the inherited ``ModelViewSet`` would hand every
        authenticated user every ``AgentRun`` in the database, ``error``,
        ``artifacts`` and ``session_ref`` included.
        """
        runs = AgentRun.objects.filter(
            workspace__slug=slug, project_id=project_id
        ).order_by("-created_at")
        return Response(AgentRunSerializer(runs, many=True).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def create(self, request, slug, project_id):
        """Wake one member on one card. Design §3① + §5's single-run lock."""
        try:
            member = AgentMember.objects.filter(
                workspace__slug=slug, project_id=project_id,
                pk=request.data.get("member_id"), is_active=True,
            ).first()
            if member is None:
                return Response(
                    {"error": "No such active AI member in this project"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            issue = Issue.objects.filter(
                workspace__slug=slug, project_id=project_id, pk=request.data.get("issue_id")
            ).first()
            if issue is None:
                return Response({"error": "No such work item"}, status=status.HTTP_400_BAD_REQUEST)

            unfinished = AgentRun.objects.filter(
                project_id=project_id, issue_id=issue.id, status__in=AGENT_UNFINISHED_STATUSES
            ).order_by("-created_at")

            # A repeat of the *same* wake-up is not an error: hand back the run that
            # is already going, so a double click cannot stack two headless runs.
            existing = unfinished.filter(member=member).first()
            if existing is not None:
                return Response(AgentRunSerializer(existing).data, status=status.HTTP_200_OK)

            if unfinished.exists():
                return Response(
                    {"error": "This work item already has a running AI member; wait for it or approve its plan."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                with transaction.atomic():
                    run = AgentRun.objects.create(
                        workspace_id=member.workspace_id,
                        project_id=project_id,
                        member=member,
                        issue=issue,
                        triggered_by_id=request.user.id,
                        created_by_id=request.user.id,
                    )
            except IntegrityError:
                # 上面那两次查询与本行 INSERT 之间，另一个请求可能已经建好了它的 run ——
                # 这就是 index 存在的理由。这里是**输掉竞速**的那一支：要件在，就该说清楚，
                # 而不是让 IntegrityError 冒到外层 except 变成一句「Could not wake the AI member」。
                # 台词与上面 `unfinished.exists()` 那支**逐字相同** —— 对调用方来说这本来就是同一件事。
                return Response(
                    {"error": "This work item already has a running AI member; wait for it or approve its plan."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                run_agent_member.delay(str(run.id))
            except Exception as e:
                # Broker 不可达（226 上的 RabbitMQ）。绝不能把这一行留在 `pending`：
                # 它属于 AGENT_UNFINISHED_STATUSES ⇒ 会永久占住设计 §5 的锁 —— 同一成员
                # 会一直拿回这个死掉的 run，别的成员一律被拒，且没有任何东西会把它重新入队。
                log_exception(e)
                run.status = AgentRunStatusEnum.FAILED.value
                run.error = "Could not reach the queue; the run was never started."
                run.finished_at = timezone.now()
                run.save(update_fields=["status", "error", "finished_at"])
                return Response(
                    {"error": "Could not reach the queue; the run was not started."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            log_exception(e)
            return Response(
                {"error": "Could not wake the AI member"},
                status=status.HTTP_400_BAD_REQUEST,
            )
