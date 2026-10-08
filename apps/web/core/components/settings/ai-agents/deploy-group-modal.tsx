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
import { CloseIcon } from "@plane/propel/icons";
import { EPillSize, EPillVariant, Pill } from "@plane/propel/pill";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
import { cn } from "@plane/utils";
// hooks
import { useAgentStore } from "@/hooks/store/use-agent-store";

type Props = {
  workspaceSlug: string;
  projectId: string;
  isOpen: boolean;
  handleClose: () => void;
};

/**
 * 「从岗位组部署」—— 项目「AI 成员」页上挨着「添加成员」的动作。
 *
 * 这是 buzz ``AddTeamToChannelDialog`` 的对应物。buzz 的**第二个** team 机制（频道里
 * 批量勾选、不携带指令）**不移植** —— 那会踩「为什么指令没生效」的坑，而这一层存在的
 * 全部理由就是那段组级正文。
 */
export const DeployGroupModal = observer(function DeployGroupModal(props: Props) {
  const { workspaceSlug, projectId, isOpen, handleClose } = props;
  // states
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // store hooks
  const { getGroupList, deployGroup } = useAgentStore();
  // translation
  const { t } = useTranslation();
  // derived values
  const groups = getGroupList();

  const closeModal = () => {
    setSelectedId(null);
    handleClose();
  };

  const handleDeploy = async () => {
    if (!selectedId) return;

    setIsSubmitting(true);
    try {
      const result = await deployGroup(workspaceSlug, projectId, selectedId);
      // 「部署了 N 个」只数**真的动了**的（新建 + 回填）—— `unchanged` 是幂等重放，
      // 报进去会让「再点一次」看起来像又装了 3 个。冲突与失败各自点名，不折叠。
      const deployed = result.created.length + result.bound.length;
      const conflicts = result.conflicts.length;
      const failures = result.failures.length;
      const troubled = conflicts > 0 || failures > 0;

      setToast({
        type: troubled ? TOAST_TYPE.ERROR : TOAST_TYPE.SUCCESS,
        title: troubled ? t("toast.error") : t("toast.success"),
        message: troubled
          ? t("ai_members.roster.deploy_result_trouble", { count: deployed, conflicts, failures })
          : t("ai_members.roster.deploy_result", { count: deployed }),
      });
      closeModal();
    } catch (error) {
      // 名册里有已删岗位 ⇒ 服务端 409 并点名。**编辑是修复路径**（本页改不了名册），
      // 所以原样透出那句话，而不是自己编一句。
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("toast.error"),
        message: (error as { error?: string })?.error ?? t("toast.error"),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={closeModal} position={EModalPosition.TOP} width={EModalWidth.XXL}>
      <div className="flex items-center justify-between border-b border-subtle px-5 py-3">
        <h3 className="text-h5-medium text-primary">{t("ai_members.roster.deploy_title")}</h3>
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
        <p className="text-caption-md-regular text-tertiary">{t("ai_members.roster.deploy_description")}</p>

        <div className="flex max-h-80 flex-col gap-2 overflow-y-auto">
          {groups.length === 0 ? (
            <p className="py-8 text-center text-caption-md-regular text-tertiary">
              {t("ai_members.roster.deploy_empty")}
            </p>
          ) : (
            groups.map((group) => (
              <button
                key={group.id}
                type="button"
                onClick={() => setSelectedId(group.id)}
                className={cn(
                  "flex flex-col gap-1 rounded-lg border px-3 py-2 text-left transition-colors",
                  selectedId === group.id ? "border-accent-strong bg-layer-2" : "border-subtle hover:bg-layer-2"
                )}
              >
                <div className="flex items-center gap-2">
                  <span className="text-body-sm-medium text-primary">{group.name}</span>
                  <Pill variant={EPillVariant.DEFAULT} size={EPillSize.SM} className="border-none">
                    {t("ai_members.groups.roster_count", { count: group.definitions.length })}
                  </Pill>
                </div>
                {group.definitions.length === 0 ? (
                  <span className="text-caption-sm-regular text-tertiary">{t("ai_members.groups.roster_none")}</span>
                ) : (
                  <span className="truncate text-caption-sm-regular text-tertiary">
                    {group.definitions.map((definition) => definition.name).join(", ")}
                  </span>
                )}
              </button>
            ))
          )}
        </div>

        <div className="flex items-center justify-end gap-2 pt-1">
          <Button variant="secondary" size="lg" onClick={closeModal} disabled={isSubmitting}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            size="lg"
            loading={isSubmitting}
            disabled={!selectedId}
            onClick={() => {
              void handleDeploy();
            }}
          >
            {t("ai_members.roster.deploy_confirm")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
