/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { EmptyStateDetailed } from "@plane/propel/empty-state";
import type { TPageNavigationTabs } from "@plane/types";
import { EUserProjectRoles } from "@plane/types";
// components
import { PageLoader } from "@/components/pages/loaders/page-loader";
import { useUserPermissions } from "@/hooks/store/user";
// plane web hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useProjectPageCreate } from "@/hooks/use-project-page-create";

type Props = {
  children: React.ReactNode;
  pageType: TPageNavigationTabs;
  storeType: EPageStoreType.PROJECT;
};

export const PagesListMainContent = observer(function PagesListMainContent(props: Props) {
  const { children, pageType, storeType } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { isAnyPageAvailable, getCurrentProjectFilteredPageIdsByTab, getCurrentProjectPageIdsByTab, loader } =
    usePageStore(storeType);
  const { allowPermissions } = useUserPermissions();
  const { createUntitledPage, isCreatingPage } = useProjectPageCreate(pageType);
  // derived values
  const pageIds = getCurrentProjectPageIdsByTab(pageType);
  const filteredPageIds = getCurrentProjectFilteredPageIdsByTab(pageType);
  const canPerformEmptyStateActions = allowPermissions(
    [EUserProjectRoles.ADMIN, EUserProjectRoles.MEMBER],
    EUserPermissionsLevel.PROJECT
  );

  if (loader === "init-loader") return <PageLoader />;
  // if no pages exist in the active page type
  if (!isAnyPageAvailable || pageIds?.length === 0) {
    if (!isAnyPageAvailable) {
      return (
        <EmptyStateDetailed
          assetKey="page"
          title={t("project_empty_state.pages.title")}
          description={t("project_empty_state.pages.description")}
          actions={[
            {
              label: t("project_empty_state.pages.cta_primary"),
              onClick: () => {
                void createUntitledPage();
              },
              variant: "primary",
              disabled: !canPerformEmptyStateActions || isCreatingPage,
            },
          ]}
        />
      );
    }
    if (pageType === "public")
      return (
        <EmptyStateDetailed
          assetKey="page"
          title={t("project_empty_state.pages.title")}
          description={t("project_empty_state.pages.description")}
          actions={[
            {
              label: t("project_empty_state.pages.cta_primary"),
              onClick: () => {
                void createUntitledPage();
              },
              variant: "primary",
              disabled: !canPerformEmptyStateActions || isCreatingPage,
            },
          ]}
        />
      );
    if (pageType === "private")
      return (
        <EmptyStateDetailed
          assetKey="page"
          title={t("project_empty_state.pages.title")}
          description={t("project_empty_state.pages.description")}
          actions={[
            {
              label: t("project_empty_state.pages.cta_primary"),
              onClick: () => {
                void createUntitledPage();
              },
              variant: "primary",
              disabled: !canPerformEmptyStateActions || isCreatingPage,
            },
          ]}
        />
      );
    if (pageType === "archived")
      return (
        <EmptyStateDetailed
          assetKey="page"
          title={t("project_empty_state.archive_pages.title")}
          description={t("project_empty_state.archive_pages.description")}
        />
      );
  }
  // if no pages match the filter criteria
  if (filteredPageIds?.length === 0)
    return (
      <EmptyStateDetailed
        assetKey="search"
        title={t("common_empty_state.search.title")}
        description={t("common_empty_state.search.description")}
      />
    );

  return <div className="h-full w-full overflow-hidden">{children}</div>;
});
