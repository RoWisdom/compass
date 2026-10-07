/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { PlusIcon, TrashIcon } from "@plane/propel/icons";
import { EPillSize, EPillVariant, Pill } from "@plane/propel/pill";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { AlertModalCore, ToggleSwitch } from "@plane/ui";
import { cn } from "@plane/utils";
// components
import { SimpleEmptyState } from "@/components/empty-state/simple-empty-state-root";
// types
import type { TAgentMember, TAgentTier } from "@/services/agent.service";
// hooks
import { useAgentStore } from "@/hooks/store/use-agent-store";
// local imports
import { AddMemberModal } from "./add-member-modal";

const TIER_LABEL: Record<TAgentTier, string> = {
  readonly: "ai_members.pool.tier_readonly",
  writer: "ai_members.pool.tier_writer",
  ledger: "ai_members.pool.tier_ledger",
};

type Props = {
  workspaceSlug: string;
  projectId: string;
};

/**
 * There is no mapping for the run statuses — phase 1 ships no copy for them, and
 * the key set is frozen. Print the enum value verbatim (honest over pretty).
 */
const statusLabel = (status: NonNullable<TAgentMember["last_run_status"]>) => status;

const runLabel = (member: TAgentMember, t: ReturnType<typeof useTranslation>["t"]) => {
  if (!member.last_run_status) return t("ai_members.roster.never_run");
  return `${t("ai_members.roster.last_run")} · ${statusLabel(member.last_run_status)}`;
};

export const MemberList = observer(function MemberList({ workspaceSlug, projectId }: Props) {
  // states
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [removingMember, setRemovingMember] = useState<TAgentMember | undefined>(undefined);
  const [isRemoving, setIsRemoving] = useState(false);
  const [togglingMemberId, setTogglingMemberId] = useState<string | null>(null);
  // store hooks
  const { getMembersByProject, updateMember, removeMember } = useAgentStore();
  // translation
  const { t } = useTranslation();
  // derived values
  const members = getMembersByProject(projectId);

  const showError = (error: unknown) => {
    setToast({
      type: TOAST_TYPE.ERROR,
      title: t("toast.error"),
      message: (error as { error?: string })?.error ?? t("toast.error"),
    });
  };

  const handleToggle = async (member: TAgentMember) => {
    setTogglingMemberId(member.id);
    try {
      await updateMember(workspaceSlug, projectId, member.id, { is_active: !member.is_active });
    } catch (error) {
      showError(error);
    } finally {
      setTogglingMemberId(null);
    }
  };

  const handleRemove = async () => {
    if (!removingMember) return;

    setIsRemoving(true);
    try {
      await removeMember(workspaceSlug, projectId, removingMember.id);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("toast.success"),
        message: removingMember.definition.name,
      });
      setRemovingMember(undefined);
    } catch (error) {
      showError(error);
    } finally {
      setIsRemoving(false);
    }
  };

  return (
    <section className="w-full">
      <div className="flex items-center justify-end">
        <Button variant="primary" size="lg" onClick={() => setIsAddOpen(true)}>
          <PlusIcon className="mr-1.5 size-4" />
          {t("ai_members.roster.add")}
        </Button>
      </div>

      <div className="mt-4 flex flex-col gap-2">
        {members.length === 0 ? (
          <div className="grid w-full place-items-center rounded-lg border border-dashed border-subtle px-4 py-16">
            <SimpleEmptyState title={t("ai_members.roster.empty")} />
          </div>
        ) : (
          members.map((member) => (
            <div
              key={member.id}
              className={cn(
                "flex items-start justify-between gap-4 rounded-lg border border-subtle px-4 py-3",
                !member.is_active && "opacity-60"
              )}
            >
              <div className="flex min-w-0 flex-col gap-1">
                <div className="flex items-center gap-2">
                  <span className="text-body-sm-medium text-primary">{member.definition.name}</span>
                  <Pill variant={EPillVariant.DEFAULT} size={EPillSize.SM} className="border-none">
                    {t(TIER_LABEL[member.definition.tier])}
                  </Pill>
                  <Pill variant={member.is_active ? EPillVariant.SUCCESS : EPillVariant.DEFAULT} size={EPillSize.SM}>
                    {member.is_active ? t("ai_members.roster.active") : t("ai_members.roster.inactive")}
                  </Pill>
                </div>
                {member.definition.description && (
                  <p className="text-caption-md-regular text-tertiary">{member.definition.description}</p>
                )}
                <p className="text-caption-sm-regular text-tertiary">{runLabel(member, t)}</p>
              </div>

              <div className="flex shrink-0 items-center gap-2">
                <ToggleSwitch
                  value={member.is_active}
                  onChange={() => {
                    void handleToggle(member);
                  }}
                  disabled={togglingMemberId === member.id}
                  size="sm"
                />
                <button
                  type="button"
                  aria-label={t("ai_members.roster.remove")}
                  className="grid size-7 place-items-center rounded text-tertiary hover:bg-layer-2 hover:text-danger-primary"
                  onClick={() => setRemovingMember(member)}
                >
                  <TrashIcon className="size-4" />
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      <AddMemberModal
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        isOpen={isAddOpen}
        handleClose={() => setIsAddOpen(false)}
      />

      <AlertModalCore
        isOpen={Boolean(removingMember)}
        handleClose={() => setRemovingMember(undefined)}
        handleSubmit={() => {
          void handleRemove();
        }}
        isSubmitting={isRemoving}
        title={t("ai_members.roster.remove_confirm_title")}
        content={t("ai_members.roster.remove_confirm_body")}
        primaryButtonText={{ loading: t("deleting"), default: t("ai_members.roster.remove") }}
        secondaryButtonText={t("common.cancel")}
      />
    </section>
  );
});
