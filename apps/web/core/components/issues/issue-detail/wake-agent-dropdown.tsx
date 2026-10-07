/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { CustomMenu } from "@plane/ui";
// hooks
import { useAgentStore } from "@/hooks/store/use-agent-store";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
};

export const WakeAgentDropdown = observer(function WakeAgentDropdown(props: Props) {
  const { workspaceSlug, projectId, issueId } = props;
  const { t } = useTranslation();
  const { fetchMembers, getMembersByProject, wake } = useAgentStore();
  const [isWaking, setIsWaking] = useState(false);

  useEffect(() => {
    // Lazy: only a work item detail page pays for this request.
    void fetchMembers(workspaceSlug, projectId).catch(() => {});
  }, [fetchMembers, workspaceSlug, projectId]);

  const members = getMembersByProject(projectId);

  const handleWake = async (memberId: string) => {
    if (isWaking) return;
    setIsWaking(true);
    try {
      await wake(workspaceSlug, projectId, issueId, memberId);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("agent.wake"),
        message: t("agent.woken"),
      });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("toast.error"),
        message: (error as { error?: string })?.error ?? t("agent.wake_failed"),
      });
    } finally {
      setIsWaking(false);
    }
  };

  return (
    <CustomMenu
      label={t("agent.wake")}
      placement="bottom-end"
      closeOnSelect
      customButton={
        <Button variant="secondary" size="lg">
          {t("agent.wake")}
        </Button>
      }
    >
      {members.length === 0 ? (
        <CustomMenu.MenuItem disabled>{t("agent.no_members")}</CustomMenu.MenuItem>
      ) : (
        members.map((member) => (
          <CustomMenu.MenuItem key={member.id} onClick={() => void handleWake(member.id)}>
            {member.name}
          </CustomMenu.MenuItem>
        ))
      )}
    </CustomMenu>
  );
});
