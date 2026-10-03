/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { EmptyStateDetailed } from "@plane/propel/empty-state";
import type { ActionButton } from "@plane/propel/empty-state";
// components
import { PageLoader } from "@/components/pages/loaders/page-loader";
import { useWikiIncludeModal } from "@/components/pages/wiki/wiki-include-modal-context";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useUserPermissions } from "@/hooks/store/user";
// services
import { canIncludeIntoCollection } from "@/services/page";

type Props = {
  collection: string;
  /** 这个键是一个**文件夹**（`?folder=`）而不是集合/分区。 */
  isFolder?: boolean;
  children: React.ReactNode;
};

export const WikiListMainContent = observer(function WikiListMainContent(props: Props) {
  const { collection, isFolder = false, children } = props;
  // plane hooks
  const { t } = useTranslation();
  // 弹窗归 `WikiIncludeModalProvider` 所有（挂在 `wiki/layout.tsx`，顶栏按钮也在用它）。
  // 这里只借 `open()` —— 不再自己挂弹窗、也不再自己存 state，否则就是两份实例。
  const { open: openIncludeModal } = useWikiIncludeModal();
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
  //
  // **文件夹也必须排掉**（执行期裁定 2）。`canIncludeIntoCollection` 自己判不出来：
  // 它认「预置键之外的一律是自建集合」，而文件夹 uuid 正好落在那一支 ⇒ 会返回 true。
  // 但那个按钮调的是**收录**端点（`WikiPageViewSet.create`），请求体只有
  // `page_ids` + `collection_id`，而 `collection_id` 只接受 `PageCollection` 的 uuid ——
  // 传文件夹 uuid 必然 404。所以这里是**显式**加一条，而不是指望上面那个谓词。
  const canIncludeHere = !isFolder && canIncludeIntoCollection(collection);
  // 两种空态里的收录入口长得完全一样（两处差的只是 title/description），抽成常量，
  // 免得同一段 20 行在两个分支里各留一份逐字副本。
  // 空态 CTA 保留：顶栏按钮常驻，这里的 CTA 是**引导**（用户明确要求保留）。
  const includeActions: ActionButton[] | undefined = canIncludeHere
    ? [
        {
          label: t("wiki_collections.menu.add_existing_page"),
          onClick: openIncludeModal,
          variant: "primary",
          disabled: !canIncludePages,
        },
      ]
    : undefined;

  if (loader === "init-loader") return <PageLoader />;

  if (!isAnyPageAvailable)
    return (
      <EmptyStateDetailed
        assetKey="page"
        title={t("project_empty_state.pages.title")}
        description={t("project_empty_state.pages.description")}
        actions={includeActions}
      />
    );

  // 空分区也必须给收录入口。`isAnyPageAvailable` 是 `Object.keys(this.data).length > 0`
  // （`workspace-page.store.ts:107-110`），而 `data` 只增不减 —— 工作区一旦加载出任何一页，
  // 上面那个分支就再也进不去了。fork 源 `pages-list-main-content.tsx:98-133` 在空 tab 时是有
  // CTA 的，这里照它的形状补上。
  // 注意：**CTA 不是这个动作唯一的入口**（顶栏按钮常驻，且本组件非空分支不渲染 CTA）。
  if (filteredPageIds?.length === 0)
    return (
      // **文件夹视图复用同一对键，尽管那里的文案说的是「此集合」**（裁定 13）：
      // 为一个词新增两个键 = 再动 19 个语言文件，不值。这是**有意的将就，不是漏改**。
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
    );

  return <div className="h-full w-full overflow-hidden">{children}</div>;
});
