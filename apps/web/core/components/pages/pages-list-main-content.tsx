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
// services
import { PAGE_NODE_TYPE_FOLDER } from "@/services/page";

type Props = {
  children: React.ReactNode;
  pageType: TPageNavigationTabs;
  storeType: EPageStoreType.PROJECT;
  /** 当前下钻的文件夹 id（来自 `?folder=`）。`null` = 根视图。 */
  folderId?: string | null;
};

export const PagesListMainContent = observer(function PagesListMainContent(props: Props) {
  const { children, pageType, storeType, folderId } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const {
    isAnyPageAvailable,
    getCurrentProjectFilteredPageIdsByTab,
    getCurrentProjectPageIdsByTab,
    loader,
    getPageNodeType,
  } = usePageStore(storeType);
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
  // 下钻中：项目级空态在这里没有意义（判的是整个项目），交给 `PagesListRoot`
  // 用它自己的文件夹空态处理。
  //
  // 判据必须与 `PagesListRoot` 的 `activeFolderId` **逐字同款**（真的指向一个文件夹），
  // 不能只看 `folderId` 真假：一个失效的 `?folder=`（书签里的文件夹已被删 / 手改成
  // 一个页面 id）在那里会退回根视图，这里若先放行，就会让「根视图 + 项目级空态」
  // 只剩一块空白面板。
  if (folderId && getPageNodeType(folderId) === PAGE_NODE_TYPE_FOLDER)
    return <div className="h-full w-full overflow-hidden">{children}</div>;
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
