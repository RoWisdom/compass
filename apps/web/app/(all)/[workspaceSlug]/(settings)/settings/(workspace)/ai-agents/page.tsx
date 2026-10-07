/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
// components
import { PageHead } from "@/components/core/page-title";
import { PostList } from "@/components/settings/ai-agents/post-list";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
// hooks
import { useAgentStore } from "@/hooks/store/use-agent-store";
import { useWorkspace } from "@/hooks/store/use-workspace";
// local imports
import type { Route } from "./+types/page";
import { WorkspaceAiAgentsSettingsHeader } from "./header";

function WorkspaceAiAgentsSettingsPage({ params }: Route.ComponentProps) {
  const { workspaceSlug } = params;
  // store hooks
  const { fetchDefinitions } = useAgentStore();
  const { currentWorkspace } = useWorkspace();
  // translation
  const { t } = useTranslation();
  // derived values
  const pageTitle = currentWorkspace?.name ? `${currentWorkspace.name} - ${t("ai_members.pool.title")}` : undefined;

  useEffect(() => {
    void fetchDefinitions(workspaceSlug);
  }, [fetchDefinitions, workspaceSlug]);

  return (
    <SettingsContentWrapper header={<WorkspaceAiAgentsSettingsHeader />}>
      <PageHead title={pageTitle} />
      <PostList />
    </SettingsContentWrapper>
  );
}

export default observer(WorkspaceAiAgentsSettingsPage);
