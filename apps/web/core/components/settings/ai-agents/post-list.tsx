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
import type { TAgentDefinition, TAgentTier } from "@/services/agent.service";
// hooks
import { useAgentStore } from "@/hooks/store/use-agent-store";
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import { PostForm } from "./post-form";

const TIER_LABEL: Record<TAgentTier, string> = {
  readonly: "ai_members.pool.tier_readonly",
  writer: "ai_members.pool.tier_writer",
  ledger: "ai_members.pool.tier_ledger",
};

export const PostList = observer(function PostList() {
  // states
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [editingPost, setEditingPost] = useState<TAgentDefinition | undefined>(undefined);
  const [deletingPost, setDeletingPost] = useState<TAgentDefinition | undefined>(undefined);
  const [isDeleting, setIsDeleting] = useState(false);
  // router
  const { workspaceSlug } = useParams();
  // store hooks
  const { getDefinitionList, deleteDefinition } = useAgentStore();
  const { allowPermissions } = useUserPermissions();
  // translation
  const { t } = useTranslation();
  // derived values
  const definitions = getDefinitionList();
  // The read side is open to every member; the write endpoints are ADMIN-only,
  // so the create/edit/delete controls are what we gate.
  const canManagePosts = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  const closeModals = () => {
    setIsCreateOpen(false);
    setEditingPost(undefined);
  };

  const handleDelete = async () => {
    if (!workspaceSlug || !deletingPost) return;

    setIsDeleting(true);
    try {
      await deleteDefinition(workspaceSlug, deletingPost.id);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("toast.success"),
        message: deletingPost.name,
      });
      setDeletingPost(undefined);
    } catch (error) {
      // The 409 from the backend already names the projects using this post —
      // surface it verbatim instead of a generic message.
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
        title={t("ai_members.pool.title")}
        description={t("ai_members.pool.description")}
        control={
          canManagePosts ? (
            <Button variant="primary" size="lg" onClick={() => setIsCreateOpen(true)}>
              <PlusIcon className="mr-1.5 size-4" />
              {t("ai_members.pool.create")}
            </Button>
          ) : undefined
        }
      />

      <div className="mt-7 flex flex-col gap-2">
        {definitions.length === 0 ? (
          <div className="grid w-full place-items-center rounded-lg border border-dashed border-subtle px-4 py-16">
            <SimpleEmptyState title={t("ai_members.pool.empty")} />
          </div>
        ) : (
          definitions.map((definition) => (
            <div
              key={definition.id}
              className="flex items-start justify-between gap-4 rounded-lg border border-subtle px-4 py-3"
            >
              <div className="flex min-w-0 flex-col gap-1">
                <div className="flex items-center gap-2">
                  <span className="text-body-sm-medium text-primary">{definition.name}</span>
                  <Pill variant={EPillVariant.DEFAULT} size={EPillSize.SM} className="border-none">
                    {t(TIER_LABEL[definition.tier])}
                  </Pill>
                </div>
                {definition.description && (
                  <p className="text-caption-md-regular text-tertiary">{definition.description}</p>
                )}
                <p className="text-caption-sm-regular text-tertiary">
                  {definition.project_count > 0
                    ? t("ai_members.pool.used_by", { count: definition.project_count })
                    : t("ai_members.pool.used_by_none")}
                </p>
              </div>

              {canManagePosts && (
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    aria-label={t("common.edit")}
                    className="grid size-7 place-items-center rounded text-tertiary hover:bg-layer-2 hover:text-primary"
                    onClick={() => setEditingPost(definition)}
                  >
                    <EditIcon className="size-4" />
                  </button>
                  <button
                    type="button"
                    aria-label={t("ai_members.pool.delete")}
                    className="grid size-7 place-items-center rounded text-tertiary hover:bg-layer-2 hover:text-danger-primary"
                    onClick={() => setDeletingPost(definition)}
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
          isOpen={isCreateOpen || Boolean(editingPost)}
          handleClose={closeModals}
          position={EModalPosition.TOP}
          width={EModalWidth.XXL}
        >
          <div className="flex items-center justify-between border-b border-subtle px-5 py-3">
            <h3 className="text-h5-medium text-primary">
              {editingPost ? t("ai_members.pool.edit_title") : t("ai_members.pool.create_title")}
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
            <PostForm
              workspaceSlug={workspaceSlug}
              definition={editingPost}
              embedded
              onSuccess={() => closeModals()}
              onCancel={closeModals}
            />
          </div>
        </ModalCore>
      )}

      <AlertModalCore
        isOpen={Boolean(deletingPost)}
        handleClose={() => setDeletingPost(undefined)}
        handleSubmit={() => {
          void handleDelete();
        }}
        isSubmitting={isDeleting}
        title={t("ai_members.pool.delete_confirm_title")}
        content={t("ai_members.pool.delete_confirm_body")}
        primaryButtonText={{ loading: t("deleting"), default: t("common.delete") }}
        secondaryButtonText={t("common.cancel")}
      />
    </section>
  );
});
