import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


def backfill_definitions(apps, schema_editor):
    """每个成员行 → 同工作区里按 name 取或建一个岗位，再把十个字段抄过去。

    同一工作区里两个项目若有同名成员，它们会**共享**同一个岗位 —— 这正是本期的目的。
    字段值取**先到的那一行**（按 created_at 升序），后到的若值不同则不动岗位
    （岗位是共享资产，谁都不能覆盖谁）。差异会在 Step 8 的对账里显形。
    """
    AgentMember = apps.get_model("db", "AgentMember")
    AgentDefinition = apps.get_model("db", "AgentDefinition")

    copies = ["color", "instructions", "skills", "tier", "model", "profile",
              "web_access", "trusted_urls", "writable_paths"]

    for member in AgentMember.objects.order_by("created_at"):
        definition = AgentDefinition.objects.filter(
            workspace_id=member.workspace_id, name=member.name, deleted_at__isnull=True
        ).first()
        if definition is None:
            definition = AgentDefinition.objects.create(
                workspace_id=member.workspace_id,
                name=member.name,
                created_by_id=member.created_by_id,
                **{field: getattr(member, field) for field in copies},
            )
        AgentMember.objects.filter(pk=member.pk).update(definition=definition)


def unbackfill_definitions(apps, schema_editor):
    """反向：把字段抄回成员行。

    同名岗位会被多个成员行共用 ⇒ 抄回去就是多份副本，这正是拆分前的样子。
    **回滚是逃生舱，不是日常路径**。
    """
    AgentMember = apps.get_model("db", "AgentMember")

    for member in AgentMember.objects.select_related("definition").order_by("created_at"):
        definition = member.definition
        if definition is None:
            continue
        AgentMember.objects.filter(pk=member.pk).update(
            name=definition.name,
            color=definition.color,
            instructions=definition.instructions,
            skills=definition.skills,
            tier=definition.tier,
            model=definition.model,
            profile=definition.profile,
            web_access=definition.web_access,
            trusted_urls=definition.trusted_urls,
            writable_paths=definition.writable_paths,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0128_agentrun_unique_unfinished_agent_run_per_issue'),
    ]

    operations = [
        migrations.CreateModel(
            name='AgentDefinition',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('description', models.CharField(blank=True, max_length=280)),
                ('instructions', models.TextField(blank=True)),
                ('skills', models.JSONField(blank=True, default=list)),
                ('tier', models.CharField(choices=[('readonly', 'Read Only'), ('writer', 'Writer'), ('ledger', 'Ledger')], default='readonly', max_length=20)),
                ('model', models.CharField(blank=True, max_length=255)),
                ('profile', models.CharField(default='compass-ai', max_length=255)),
                ('web_access', models.BooleanField(default=False)),
                ('trusted_urls', models.JSONField(blank=True, default=list)),
                ('writable_paths', models.JSONField(blank=True, default=list)),
                ('color', models.CharField(blank=True, max_length=255)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agent_definitions', to='db.workspace')),
            ],
            options={
                'verbose_name': 'Agent Definition',
                'verbose_name_plural': 'Agent Definitions',
                'db_table': 'agent_definitions',
                'ordering': ('-created_at',),
            },
        ),
        migrations.AddConstraint(
            model_name='agentdefinition',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('workspace', 'name'), name='unique_agent_definition_name_per_workspace'),
        ),
        migrations.AddField(
            model_name='agentmember',
            name='definition',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='members', to='db.agentdefinition'),
        ),
        migrations.RunPython(backfill_definitions, unbackfill_definitions),
        migrations.AlterField(
            model_name='agentmember',
            name='definition',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='members', to='db.agentdefinition'),
        ),
        migrations.RemoveField(
            model_name='agentmember',
            name='color',
        ),
        migrations.RemoveField(
            model_name='agentmember',
            name='instructions',
        ),
        migrations.RemoveField(
            model_name='agentmember',
            name='model',
        ),
        migrations.RemoveField(
            model_name='agentmember',
            name='name',
        ),
        migrations.RemoveField(
            model_name='agentmember',
            name='profile',
        ),
        migrations.RemoveField(
            model_name='agentmember',
            name='skills',
        ),
        migrations.RemoveField(
            model_name='agentmember',
            name='tier',
        ),
        migrations.RemoveField(
            model_name='agentmember',
            name='trusted_urls',
        ),
        migrations.RemoveField(
            model_name='agentmember',
            name='web_access',
        ),
        migrations.RemoveField(
            model_name='agentmember',
            name='writable_paths',
        ),
        migrations.RemoveConstraint(
            model_name='agentmember',
            name='unique_agent_member_name_per_project',
        ),
        migrations.AddConstraint(
            model_name='agentmember',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('project', 'definition'), name='unique_agent_member_definition_per_project'),
        ),
    ]
