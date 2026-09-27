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
import type { ActionButton } from "@plane/propel/empty-state";
// components
import { PageLoader } from "@/components/pages/loaders/page-loader";
import { AddExistingPageModal } from "@/components/pages/wiki/add-existing-page-modal";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useUserPermissions } from "@/hooks/store/user";
// services
import { canIncludeIntoCollection } from "@/services/page";

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
  // 只有 `general` 与自建集合能接收收录。`private`/`shared`/`archived` 是**派生**分区：
  // 页面落在哪儿由 access/archived_at 决定，不由 collection_id 决定（见
  // `canIncludeIntoCollection` 的注释）。在那三个分区里给收录入口，用户选中的页面会跑到别处去。
  const canIncludeHere = canIncludeIntoCollection(collection);
  // 两种空态里的收录入口长得完全一样（两处差的只是 title/description），抽成常量，
  // 免得同一段 20 行在两个分支里各留一份逐字副本。
  const includeActions: ActionButton[] | undefined = canIncludeHere
    ? [
        {
          label: t("wiki_collections.menu.add_existing_page"),
          onClick: () => setIsAddExistingModalOpen(true),
          variant: "primary",
          disabled: !canIncludePages,
        },
      ]
    : undefined;
  // 弹窗挂载也门控 `canIncludeHere`：换分区时本组件不重建，`isAddExistingModalOpen` 这个 state
  // 会留着 —— 只门控按钮的话，在 general 打开弹窗再切到 private，弹窗会挂在那儿继续可选页。
  const includeModal = canIncludeHere ? (
    <AddExistingPageModal
      isOpen={isAddExistingModalOpen}
      collection={collection}
      handleClose={() => setIsAddExistingModalOpen(false)}
    />
  ) : null;

  if (loader === "init-loader") return <PageLoader />;

  if (!isAnyPageAvailable)
    return (
      <>
        <EmptyStateDetailed
          assetKey="page"
          title={t("project_empty_state.pages.title")}
          description={t("project_empty_state.pages.description")}
          actions={includeActions}
        />
        {includeModal}
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
          actions={includeActions}
        />
        {includeModal}
      </>
    );

  return <div className="h-full w-full overflow-hidden">{children}</div>;
});
