/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
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
   * 当前页的**祖先链** `[最顶, …, 直接父级]`（**不含当前页本身**，它由右侧那颗
   * 下拉自己代表）。
   *
   * 详情路由的 layout 也调了 `fetchPagesList`（同一个 `PROJECT_PAGES_<id>` SWR key，
   * `?scope=all`），所以 `pageParentIds` 在这条路由上**有值** —— 这是这半截面包屑
   * 能成立的前提，不是碰巧。`depth` 封顶防环。
   */
  const folderChain = useMemo(() => {
    const chain: string[] = [];
    if (!pageId) return chain;
    let cursor: string | null = pageParentIds[pageId.toString()] ?? null;
    let depth = 0;
    while (cursor && depth < 32) {
      chain.unshift(cursor);
      cursor = pageParentIds[cursor] ?? null;
      depth += 1;
    }
    return chain;
  }, [pageId, pageParentIds]);

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
