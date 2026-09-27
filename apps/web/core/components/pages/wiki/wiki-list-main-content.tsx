/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { EmptyStateDetailed } from "@plane/propel/empty-state";
// components
import { PageLoader } from "@/components/pages/loaders/page-loader";
import { AddExistingPageModal } from "@/components/pages/wiki/add-existing-page-modal";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useUserPermissions } from "@/hooks/store/user";

type Props = {
  collection: string;
  children: React.ReactNode;
};

export const WikiListMainContent = observer(function WikiListMainContent(props: Props) {
  const { collection, children } = props;
  // states
  const [isAddExistingModalOpen, setIsAddExistingModalOpen] = useState(false);
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { isAnyPageAvailable, getFilteredPageIdsByCollection, loader } = usePageStore(EPageStoreType.WORKSPACE);
  const { allowPermissions } = useUserPermissions();
  // derived values
  const filteredPageIds = getFilteredPageIdsByCollection(collection);
  const canIncludePages = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.WORKSPACE
  );

  if (loader === "init-loader") return <PageLoader />;

  if (!isAnyPageAvailable)
    return (
      <>
        <EmptyStateDetailed
          assetKey="page"
          title={t("project_empty_state.pages.title")}
          description={t("project_empty_state.pages.description")}
          actions={[
            {
              label: t("wiki_collections.menu.add_existing_page"),
              onClick: () => setIsAddExistingModalOpen(true),
              variant: "primary",
              disabled: !canIncludePages,
            },
          ]}
        />
        <AddExistingPageModal
          isOpen={isAddExistingModalOpen}
          collection={collection}
          handleClose={() => setIsAddExistingModalOpen(false)}
        />
      </>
    );

  // 空分区也必须给收录入口。`isAnyPageAvailable` 是 `Object.keys(this.data).length > 0`
  // （`workspace-page.store.ts:102-105`），而 `data` 只增不减 —— 工作区一旦加载出任何一页，
  // 上面那个分支就再也进不去了。渲染空分区而不给 CTA，等于「收录」这个动作在整个 UI 里不可达。
  // fork 源 `pages-list-main-content.tsx:98-133` 在空 tab 时是有 CTA 的，这里照它的形状补上。
  if (filteredPageIds?.length === 0)
    return (
      <>
        <EmptyStateDetailed
          assetKey="page"
          // 用 `list.no_pages_*` 而不是 `common_empty_state.search.*`：wiki 里没有任何 UI 会写
          // `filters.searchQuery`，所以这个分支的真实触发条件就是「分区为空」，不是「搜不到」。
          // 这两个键在 en 与 zh-CN 都已存在（「还没有页面」/「此集合当前没有任何页面。」），
          // 且全 apps/web 无人消费 —— 不新增文案。
          title={t("wiki_collections.list.no_pages_title")}
          description={t("wiki_collections.list.no_pages_description")}
          actions={[
            {
              label: t("wiki_collections.menu.add_existing_page"),
              onClick: () => setIsAddExistingModalOpen(true),
              variant: "primary",
              disabled: !canIncludePages,
            },
          ]}
        />
        <AddExistingPageModal
          isOpen={isAddExistingModalOpen}
          collection={collection}
          handleClose={() => setIsAddExistingModalOpen(false)}
        />
      </>
    );

  return <div className="h-full w-full overflow-hidden">{children}</div>;
});
