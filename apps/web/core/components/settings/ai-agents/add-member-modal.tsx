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
import { CloseIcon, PlusIcon } from "@plane/propel/icons";
import { EPillSize, EPillVariant, Pill } from "@plane/propel/pill";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { Checkbox, EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
// types
import type { TAgentDefinition, TAgentTier } from "@/services/agent.service";
// hooks
import { useAgentStore } from "@/hooks/store/use-agent-store";
// local imports
import { PostForm } from "./post-form";

const TIER_LABEL: Record<TAgentTier, string> = {
  readonly: "ai_members.pool.tier_readonly",
  writer: "ai_members.pool.tier_writer",
  ledger: "ai_members.pool.tier_ledger",
};

type Props = {
  workspaceSlug: string;
  projectId: string;
  isOpen: boolean;
  handleClose: () => void;
};

export const AddMemberModal = observer(function AddMemberModal(props: Props) {
  const { workspaceSlug, projectId, isOpen, handleClose } = props;
  // states
  const [selected, setSelected] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isCreatingPost, setIsCreatingPost] = useState(false);
  // store hooks
  const { getDefinitionList, getMembersByProject, addMember } = useAgentStore();
  // translation
  const { t } = useTranslation();
  // derived values
  // The membership API returns the nested `definition` and no `definition_id`,
  // so the posts already in this project are matched by `definition.id`.
  const memberDefinitionIds = new Set(getMembersByProject(projectId).map((member) => member.definition.id));
  const candidates = getDefinitionList().filter((definition) => !memberDefinitionIds.has(definition.id));

  const showError = (error: unknown) => {
    setToast({
      type: TOAST_TYPE.ERROR,
      title: t("toast.error"),
      message: (error as { error?: string })?.error ?? t("toast.error"),
    });
  };

  const closeModal = () => {
    setSelected([]);
    setIsCreatingPost(false);
    handleClose();
  };

  const toggleSelected = (definitionId: string) =>
    setSelected((prev) =>
      prev.includes(definitionId) ? prev.filter((id) => id !== definitionId) : [...prev, definitionId]
    );

  const handleConfirm = async () => {
    if (selected.length === 0) return;

    setIsSubmitting(true);
    try {
      // The candidate set is the whole pool (a handful), so N posts is enough —
      // a bespoke batch endpoint for this isn't worth the non-standard shape.
      const results = await Promise.allSettled(selected.map((id) => addMember(workspaceSlug, projectId, id)));
      const failed = results.filter((result) => result.status === "rejected").length;
      if (failed) {
        setToast({
          type: TOAST_TYPE.ERROR,
          title: t("toast.error"),
          message: `${failed} / ${selected.length}`,
        });
      } else {
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t("toast.success"),
          message: `${selected.length}`,
        });
      }
      closeModal();
    } finally {
      setIsSubmitting(false);
    }
  };

  // A post created from inside this dialog is added to the project right away.
  const handleCreated = async (definition: TAgentDefinition) => {
    try {
      await addMember(workspaceSlug, projectId, definition.id);
      setToast({ type: TOAST_TYPE.SUCCESS, title: t("toast.success"), message: definition.name });
      closeModal();
    } catch (error) {
      showError(error);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={closeModal} position={EModalPosition.TOP} width={EModalWidth.XXL}>
      <div className="flex items-center justify-between border-b border-subtle px-5 py-3">
        <h3 className="text-h5-medium text-primary">{t("ai_members.roster.add_title")}</h3>
        <button
          type="button"
          aria-label={t("close")}
          className="grid size-7 place-items-center rounded text-tertiary hover:bg-layer-2 hover:text-primary"
          onClick={closeModal}
        >
          <CloseIcon className="size-4" />
        </button>
      </div>

      <div className="flex flex-col gap-4 px-5 py-4">
        {isCreatingPost ? (
          <PostForm
            workspaceSlug={workspaceSlug}
            embedded
            onSuccess={(definition) => {
              void handleCreated(definition);
            }}
            onCancel={() => setIsCreatingPost(false)}
          />
        ) : (
          <>
            <p className="text-caption-md-regular text-tertiary">{t("ai_members.roster.add_description")}</p>

            {candidates.length === 0 ? (
              <div className="grid w-full place-items-center rounded-lg border border-dashed border-subtle px-4 py-10">
                <p className="text-caption-md-regular text-tertiary">{t("ai_members.roster.add_none_left")}</p>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                {candidates.map((definition) => (
                  // The label wraps the checkbox plus its copy — same shape (and
                  // the same lint exception) as post-form's tier radios.
                  // oxlint-disable-next-line jsx_a11y/label-has-associated-control
                  <label
                    key={definition.id}
                    className="flex cursor-pointer items-start gap-3 rounded-lg border border-subtle px-3 py-2"
                  >
                    <Checkbox
                      checked={selected.includes(definition.id)}
                      onChange={() => toggleSelected(definition.id)}
                      containerClassName="mt-0.5"
                    />
                    <span className="flex min-w-0 flex-col gap-0.5">
                      <span className="flex items-center gap-2">
                        <span className="text-body-sm-medium text-primary">{definition.name}</span>
                        <Pill variant={EPillVariant.DEFAULT} size={EPillSize.SM} className="border-none">
                          {t(TIER_LABEL[definition.tier])}
                        </Pill>
                      </span>
                      {definition.description && (
                        <span className="text-caption-md-regular text-tertiary">{definition.description}</span>
                      )}
                    </span>
                  </label>
                ))}
              </div>
            )}

            <div className="flex items-center justify-between pt-1">
              <Button variant="secondary" size="lg" onClick={() => setIsCreatingPost(true)}>
                <PlusIcon className="mr-1.5 size-4" />
                {t("ai_members.roster.create_post")}
              </Button>
              <div className="flex items-center gap-2">
                <Button variant="secondary" size="lg" onClick={closeModal} disabled={isSubmitting}>
                  {t("common.cancel")}
                </Button>
                <Button
                  variant="primary"
                  size="lg"
                  loading={isSubmitting}
                  disabled={selected.length === 0}
                  onClick={() => {
                    void handleConfirm();
                  }}
                >
                  {t("ai_members.roster.add_confirm")}
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </ModalCore>
  );
});
