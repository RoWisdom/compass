/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "react-router";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { CloseIcon, EditIcon, PlusIcon, TrashIcon } from "@plane/propel/icons";
import { EPillSize, EPillVariant, Pill } from "@plane/propel/pill";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { AlertModalCore, EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
// components
import { SimpleEmptyState } from "@/components/empty-state/simple-empty-state-root";
import { SettingsHeading } from "@/components/settings/heading";
// types
import type { TAgentGroup } from "@/services/agent.service";
// hooks
import { useAgentStore } from "@/hooks/store/use-agent-store";
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import { GroupForm } from "./group-form";

/** 卡片上直接列几个岗位名，剩下的折成 `+N`。 */
const ROSTER_PREVIEW_LIMIT = 4;

/**
 * 「岗位组」区块 —— 工作区「AI 岗位」页上的**第二个堆叠区块**（照 buzz 的 AgentsView：
 * 两个堆叠 grid，teams 在后）。
 *
 * 为什么不开新设置页：加一个设置页要改五处（types 联合 / constants 映射 + GROUPED /
 * item-icon / 路由两件套 / i18n），而这一层本来就和岗位是同一件事的两面 —— 一个岗位组
 * 就是「一组岗位」，放在同一页上还能顺带告诉用户岗位是从哪儿挑的。
 */
export const GroupList = observer(function GroupList() {
  // states
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<TAgentGroup | undefined>(undefined);
  const [deletingGroup, setDeletingGroup] = useState<TAgentGroup | undefined>(undefined);
  const [isDeleting, setIsDeleting] = useState(false);
  // router
  const { workspaceSlug } = useParams();
  // store hooks
  const { getGroupList, deleteGroup } = useAgentStore();
  const { allowPermissions } = useUserPermissions();
  // translation
  const { t } = useTranslation();
  // derived values
  const groups = getGroupList();
  const canManageGroups = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  const closeModals = () => {
    setIsCreateOpen(false);
    setEditingGroup(undefined);
  };

  const handleDelete = async () => {
    if (!workspaceSlug || !deletingGroup) return;

    setIsDeleting(true);
    try {
      await deleteGroup(workspaceSlug, deletingGroup.id);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("toast.success"),
        message: deletingGroup.name,
      });
      setDeletingGroup(undefined);
    } catch (error) {
      // 后端的 409 已经点名了还绑着这个组的项目 —— 原样透出去。
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("toast.error"),
        message: (error as { error?: string })?.error ?? t("toast.error"),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <section className="w-full">
      <SettingsHeading
        title={t("ai_members.groups.title")}
        description={t("ai_members.groups.description")}
        control={
          canManageGroups ? (
            <Button variant="primary" size="lg" onClick={() => setIsCreateOpen(true)}>
              <PlusIcon className="mr-1.5 size-4" />
              {t("ai_members.groups.create")}
            </Button>
          ) : undefined
        }
      />

      <div className="mt-7 flex flex-col gap-2">
        {groups.length === 0 ? (
          <div className="grid w-full place-items-center rounded-lg border border-dashed border-subtle px-4 py-16">
            <SimpleEmptyState title={t("ai_members.groups.empty")} />
          </div>
        ) : (
          groups.map((group) => (
            <div
              key={group.id}
              className="flex items-start justify-between gap-4 rounded-lg border border-subtle px-4 py-3"
            >
              <div className="flex min-w-0 flex-col gap-1">
                <div className="flex items-center gap-2">
                  <span className="text-body-sm-medium text-primary">{group.name}</span>
                  <Pill variant={EPillVariant.DEFAULT} size={EPillSize.SM} className="border-none">
                    {t("ai_members.groups.roster_count", { count: group.definitions.length })}
                  </Pill>
                </div>
                {group.description && <p className="text-caption-md-regular text-tertiary">{group.description}</p>}
                {/* 名册的预览：只列**活着的**岗位（服务端那份嵌套只读名册本就如此）。 */}
                {group.definitions.length === 0 ? (
                  <p className="text-caption-sm-regular text-tertiary">{t("ai_members.groups.roster_none")}</p>
                ) : (
                  <div className="flex flex-wrap items-center gap-1.5">
                    {group.definitions.slice(0, ROSTER_PREVIEW_LIMIT).map((definition) => (
                      <Pill
                        key={definition.id}
                        variant={EPillVariant.DEFAULT}
                        size={EPillSize.SM}
                        className="border-none"
                      >
                        {definition.name}
                      </Pill>
                    ))}
                    {group.definitions.length > ROSTER_PREVIEW_LIMIT && (
                      <span className="text-caption-sm-regular text-tertiary">
                        +{group.definitions.length - ROSTER_PREVIEW_LIMIT}
                      </span>
                    )}
                  </div>
                )}
                <p className="text-caption-sm-regular text-tertiary">
                  {group.project_count > 0
                    ? t("ai_members.groups.used_by", { count: group.project_count })
                    : t("ai_members.groups.used_by_none")}
                </p>
              </div>

              {canManageGroups && (
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    aria-label={t("common.edit")}
                    className="grid size-7 place-items-center rounded text-tertiary hover:bg-layer-2 hover:text-primary"
                    onClick={() => setEditingGroup(group)}
                  >
                    <EditIcon className="size-4" />
                  </button>
                  <button
                    type="button"
                    aria-label={t("ai_members.groups.delete")}
                    className="grid size-7 place-items-center rounded text-tertiary hover:bg-layer-2 hover:text-danger-primary"
                    onClick={() => setDeletingGroup(group)}
                  >
                    <TrashIcon className="size-4" />
                  </button>
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {workspaceSlug && (
        <ModalCore
          isOpen={isCreateOpen || Boolean(editingGroup)}
          handleClose={closeModals}
          position={EModalPosition.TOP}
          width={EModalWidth.XXL}
        >
          <div className="flex items-center justify-between border-b border-subtle px-5 py-3">
            <h3 className="text-h5-medium text-primary">
              {editingGroup ? t("ai_members.groups.edit_title") : t("ai_members.groups.create_title")}
            </h3>
            <button
              type="button"
              aria-label={t("close")}
              className="grid size-7 place-items-center rounded text-tertiary hover:bg-layer-2 hover:text-primary"
              onClick={closeModals}
            >
              <CloseIcon className="size-4" />
            </button>
          </div>
          <div className="px-5 py-4">
            <GroupForm
              workspaceSlug={workspaceSlug}
              group={editingGroup}
              onSuccess={() => closeModals()}
              onCancel={closeModals}
            />
          </div>
        </ModalCore>
      )}

      <AlertModalCore
        isOpen={Boolean(deletingGroup)}
        handleClose={() => setDeletingGroup(undefined)}
        handleSubmit={() => {
          void handleDelete();
        }}
        isSubmitting={isDeleting}
        title={t("ai_members.groups.delete_confirm_title")}
        content={t("ai_members.groups.delete_confirm_body")}
        primaryButtonText={{ loading: t("deleting"), default: t("common.delete") }}
        secondaryButtonText={t("common.cancel")}
      />
    </section>
  );
});
