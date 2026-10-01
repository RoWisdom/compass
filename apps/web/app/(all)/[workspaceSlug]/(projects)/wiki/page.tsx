/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
// components
import { PageHead } from "@/components/core/page-title";
import { WikiHome } from "@/components/pages/wiki/wiki-home";
import { WikiListMainContent } from "@/components/pages/wiki/wiki-list-main-content";
import { WikiListRoot } from "@/components/pages/wiki/wiki-list-root";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import type { Route } from "./+types/page";

function WikiPage({ params }: Route.ComponentProps) {
  const { workspaceSlug } = params;
  const searchParams = useSearchParams();
  // **`?collection` 缺省 = 首页**，不兜底成 `"general"`。
  //
  // 这个分界必须与侧栏的选中判据（`!activePageId && !explicitCollection`）和顶栏的
  // `isHome` 是同一条：若这里用 `?? "general"` 兜底后再判，首页会去拉「常规」的列表，
  // 而侧栏那颗「首页」也亮着 —— 列表内容与高亮行说的是两个地方。
  // 顺带也省掉首页上那次没用的 `fetchPagesList`。
  const explicitCollection = searchParams.get("collection");

  if (explicitCollection === null)
    return (
      <>
        <PageHead title="Wiki" />
        <WikiHome workspaceSlug={workspaceSlug} />
      </>
    );

  return (
    <>
      <PageHead title="Wiki" />
      <WikiListView workspaceSlug={workspaceSlug} collection={explicitCollection} />
    </>
  );
}

const WikiListView = observer(function WikiListView(props: { workspaceSlug: string; collection: string }) {
  const { workspaceSlug, collection } = props;
  // store hooks
  const { fetchPagesList } = usePageStore(EPageStoreType.WORKSPACE);

  useSWR(
    workspaceSlug && collection ? `WORKSPACE_PAGES_${workspaceSlug}_${collection}` : null,
    workspaceSlug && collection ? () => fetchPagesList(workspaceSlug, collection) : null
  );

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden">
      <WikiListMainContent collection={collection}>
        <WikiListRoot collection={collection} />
      </WikiListMainContent>
    </div>
  );
});

export default WikiPage;
