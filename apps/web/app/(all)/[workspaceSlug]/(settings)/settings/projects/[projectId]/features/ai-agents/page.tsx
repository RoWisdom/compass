/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect } from "react";
import { observer } from "mobx-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
// components
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { MemberList } from "@/components/settings/ai-agents/member-list";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { SettingsHeading } from "@/components/settings/heading";
// hooks
import { useAgentStore } from "@/hooks/store/use-agent-store";
import { useProject } from "@/hooks/store/use-project";
import { useUserPermissions } from "@/hooks/store/user";
// local imports
import type { Route } from "./+types/page";
import { FeaturesAiAgentsProjectSettingsHeader } from "./header";

function FeaturesAiAgentsProjectSettingsPage({ params }: Route.ComponentProps) {
  const { workspaceSlug, projectId } = params;
  // store hooks
  const { fetchDefinitions, fetchGroups, fetchMembers } = useAgentStore();
  const { currentProjectDetails } = useProject();
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();
  // translation
  const { t } = useTranslation();
  // derived values
  const pageTitle = currentProjectDetails?.name
    ? `${currentProjectDetails.name} settings - ${t("ai_members.roster.title")}`
    : undefined;
  const canPerformProjectAdminActions = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.PROJECT);

  useEffect(() => {
    // 岗位组也要拉：本页的「从岗位组部署」要列工作区的组。
    void Promise.all([
      fetchDefinitions(workspaceSlug),
      fetchGroups(workspaceSlug),
      fetchMembers(workspaceSlug, projectId),
    ]).catch(() => {});
  }, [fetchDefinitions, fetchGroups, fetchMembers, workspaceSlug, projectId]);

  if (workspaceUserInfo && !canPerformProjectAdminActions) {
    return <NotAuthorizedView section="settings" isProjectView className="h-auto" />;
  }

  return (
    <SettingsContentWrapper header={<FeaturesAiAgentsProjectSettingsHeader />}>
      <PageHead title={pageTitle} />
      <section className="w-full">
        <SettingsHeading title={t("ai_members.roster.title")} description={t("ai_members.roster.description")} />
        <div className="mt-7">
          <MemberList workspaceSlug={workspaceSlug} projectId={projectId} />
        </div>
      </section>
    </SettingsContentWrapper>
  );
}

export default observer(FeaturesAiAgentsProjectSettingsPage);
