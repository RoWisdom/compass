/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
// components
import { PageHead } from "@/components/core/page-title";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { WorkspacePaths } from "@/components/workspace/settings/workspace-paths";
// hooks
import { useWorkspace } from "@/hooks/store/use-workspace";
// local imports
import { WorkspacePathsSettingsHeader } from "./header";

function WorkspacePathsSettingsPage() {
  // store hooks
  const { currentWorkspace } = useWorkspace();
  const { t } = useTranslation();
  // derived values
  const pageTitle = currentWorkspace?.name
    ? `${currentWorkspace.name} - ${t("workspace_settings.settings.paths.title")}`
    : undefined;

  return (
    <SettingsContentWrapper header={<WorkspacePathsSettingsHeader />}>
      <PageHead title={pageTitle} />
      <WorkspacePaths />
    </SettingsContentWrapper>
  );
}

export default observer(WorkspacePathsSettingsPage);
