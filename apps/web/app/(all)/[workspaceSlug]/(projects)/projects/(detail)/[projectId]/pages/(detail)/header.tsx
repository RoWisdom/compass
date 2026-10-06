/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import { PageIcon } from "@plane/propel/icons";
import type { ICustomSearchSelectOption } from "@plane/types";
import { Breadcrumbs, Header, BreadcrumbNavigationSearchDropdown } from "@plane/ui";
import { getPageName } from "@plane/utils";
// components
import { BreadcrumbLink } from "@/components/common/breadcrumb-link";
import { PageAccessIcon } from "@/components/common/page-access-icon";
import { SwitcherIcon, SwitcherLabel } from "@/components/common/switcher-label";
import { PageHeaderActions } from "@/components/pages/header/actions";
import { PageSyncingBadge } from "@/components/pages/header/syncing-badge";
import { CommonProjectBreadcrumbs } from "@/components/breadcrumbs/common";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useAppRouter } from "@/hooks/use-app-router";
import { EPageStoreType, usePage, usePageStore } from "@/hooks/store";
// services
import { PAGE_NODE_TYPE_FOLDER } from "@/services/page";

export interface IPagesHeaderProps {
  showButton?: boolean;
}

const storeType = EPageStoreType.PROJECT;

/**
 * 沿 `pageParentIds` 往上走到根，返回 `[最顶, …, 直接父级]`（**不含起点自己**）。
 *
 * `seen` 而不是「深度封顶 32」：`seen` 让这个循环**可证终止**（每轮都往集合里添一个
 * 新 id，id 有限），且顺带保证链里不会出现重复 id —— 一个环在 `Map` 里会让 React
 * 撞上重复 key。封一个魔数只是把「死循环」换成「截断的错链」，不如直接判环。
 */
const collectAncestors = (pageParentIds: Record<string, string | null>, startId: string | undefined): string[] => {
  const chain: string[] = [];
  if (!startId) return chain;
  const seen = new Set<string>();
  let cursor: string | null = pageParentIds[startId] ?? null;
  while (cursor && !seen.has(cursor)) {
    seen.add(cursor);
    chain.unshift(cursor);
    cursor = pageParentIds[cursor] ?? null;
  }
  return chain;
};

export const PageDetailsHeader = observer(function PageDetailsHeader() {
  // router
  const router = useAppRouter();
  const { workspaceSlug, pageId, projectId } = useParams();
  // store hooks
  const { loader } = useProject();
  const { getPageById, getCurrentProjectPageIds, pageParentIds, getPageNodeType } = usePageStore(storeType);
  const page = usePage({
    pageId: pageId?.toString() ?? "",
    storeType,
  });
  // derived values
  const projectPageIds = getCurrentProjectPageIds(projectId?.toString());

  /**
   * 当前页的**祖先链**（不含当前页本身 —— 它由右侧那颗下拉代表）。
   *
   * 详情路由的 layout 也调了 `fetchPagesList`（同一个 `PROJECT_PAGES_<id>` SWR key、
   * `?scope=all`），所以 `pageParentIds` 在这条路由上**有值** —— 这是这半截面包屑
   * 能成立的前提，不是碰巧。
   *
   * **此处故意不包 `useMemo`**：`pageParentIds` 是 MobX observable，只在原对象上
   * `set`/`unset`（`project-page.store.ts:125,142,303`），**地址从不换**。拿它当
   * useMemo 的依赖，硬刷新 / 直接开这条 URL 时首帧（store 还空着）算出的空链会被
   * **永久缓存** —— 数据灌满后组件确实会重渲，但依赖没变、memo 不回算，面包屑就
   * 少了中间那层，正是这一版要修的症状。写在渲染体里，每次渲染都重读，MobX 才跟得住。
   * 开销是每个祖先一次查表（≤ 树深），可以忽略。
   */
  const folderChain = collectAncestors(pageParentIds, pageId?.toString());

  /**
   * 祖先节点的落点：文件夹 → **下钻视图**（`?folder=<id>`，与列表里点文件夹行同一
   * 落点），页面 → 它自己的详情页。正常结构下祖先只会是文件夹（单亲、父级都是
   * 文件夹），这里仍分开判 —— 免得哪天树结构松了，面包屑悄悄点进一个不存在的下钻。
   */
  const buildAncestorHref = (id: string) =>
    getPageNodeType(id) === PAGE_NODE_TYPE_FOLDER
      ? `/${workspaceSlug}/projects/${projectId}/pages/?folder=${id}`
      : `/${workspaceSlug}/projects/${projectId}/pages/${id}`;

  const switcherOptions = projectPageIds
    .map((id) => {
      const _page = id === pageId ? page : getPageById(id);
      if (!_page) return;
      return {
        value: _page.id,
        query: _page.name,
        content: (
          <div className="flex items-center justify-between gap-2">
            <SwitcherLabel logo_props={_page.logo_props} name={getPageName(_page.name)} LabelIcon={PageIcon} />
            <PageAccessIcon {..._page} />
          </div>
        ),
      };
    })
    .filter((option) => option !== undefined) as ICustomSearchSelectOption[];

  if (!page) return null;

  return (
    <Header>
      <Header.LeftItem>
        <div>
          <Breadcrumbs isLoading={loader === "init-loader"}>
            <CommonProjectBreadcrumbs workspaceSlug={workspaceSlug?.toString()} projectId={projectId?.toString()} />
            <Breadcrumbs.Item
              component={
                <BreadcrumbLink
                  label="Pages"
                  href={`/${workspaceSlug}/projects/${projectId}/pages/`}
                  icon={<PageIcon className="h-4 w-4 text-tertiary" />}
                />
              }
            />

            {/* 中间的文件夹祖先（罗盘 Round J）。缺了它们，从文件夹里点开的页面
                面包屑会只剩 `Pages → 这一页`，把「这一页在哪个文件夹里」整个丢掉。 */}
            {folderChain.map((id) => (
              <Breadcrumbs.Item
                key={id}
                component={<BreadcrumbLink label={getPageName(getPageById(id)?.name)} href={buildAncestorHref(id)} />}
              />
            ))}

            <Breadcrumbs.Item
              component={
                <BreadcrumbNavigationSearchDropdown
                  selectedItem={pageId?.toString() ?? ""}
                  navigationItems={switcherOptions}
                  onChange={(value: string) => {
                    router.push(`/${workspaceSlug}/projects/${projectId}/pages/${value}`);
                  }}
                  title={getPageName(page?.name)}
                  icon={
                    <Breadcrumbs.Icon>
                      <SwitcherIcon logo_props={page.logo_props} LabelIcon={PageIcon} size={16} />
                    </Breadcrumbs.Icon>
                  }
                  isLast
                />
              }
            />
          </Breadcrumbs>
        </div>
      </Header.LeftItem>
      <Header.RightItem>
        <PageSyncingBadge syncStatus={page.isSyncingWithServer} />
        <PageHeaderActions page={page} storeType={storeType} />
      </Header.RightItem>
    </Header>
  );
});
