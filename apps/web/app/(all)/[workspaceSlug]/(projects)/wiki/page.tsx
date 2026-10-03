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
  /**
   * 三个参数不是三选一，而是**一条优先级链**（执行期裁定 1）：
   *
   *   `?folder=`  >  `?collection=`  >  都没有（= 首页）
   *
   * **「首页」= 两个参数都没写** —— 与 `header.tsx` 的 `isHome`、与侧栏 Home 行的
   * `isActive` 是同一条判据，三处必须同批改。少改一处就是一处显眼的错：
   * 这里会把 `?folder=` 当首页去渲染 `WikiHome`，文件夹视图根本不出现。
   */
  const explicitFolder = searchParams.get("folder");
  const explicitCollection = searchParams.get("collection");

  // **`?folder=` 优先**：两者同时出现时按文件夹走。不这样定就得为"同时写"编第三种
  // 语义，而没有任何 UI 会产出这种 URL（侧栏两个入口各自只拼一个参数）。
  if (explicitFolder !== null)
    return (
      <>
        <PageHead title="Wiki" />
        <WikiListView workspaceSlug={workspaceSlug} collection={explicitFolder} isFolder />
      </>
    );

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

const WikiListView = observer(function WikiListView(props: {
  workspaceSlug: string;
  collection: string;
  isFolder?: boolean;
}) {
  const { workspaceSlug, collection, isFolder = false } = props;
  // store hooks
  const { fetchPagesList, fetchFolderPages } = usePageStore(EPageStoreType.WORKSPACE);

  // 两种视图**各用一条 SWR 键前缀**（`WIKI_FOLDER_` / `WIKI_PAGES_`）而不是共用一个：
  // 键是字符串，而文件夹 uuid 与集合 uuid 都是 uuid —— 共用前缀就只能靠"它们不会撞"
  // 这个假设。分成两个前缀，撞不撞都无所谓。
  //
  // **端点也随之分叉**：`?collection=` 打 collection 那条，`?folder=` 打 folder 那条。
  useSWR(
    workspaceSlug && collection
      ? `${isFolder ? "WIKI_FOLDER_PAGES" : "WIKI_PAGES"}_${workspaceSlug}_${collection}`
      : null,
    workspaceSlug && collection
      ? () => (isFolder ? fetchFolderPages(workspaceSlug, collection) : fetchPagesList(workspaceSlug, collection))
      : null
  );

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden">
      <WikiListMainContent collection={collection} isFolder={isFolder}>
        <WikiListRoot collection={collection} isFolder={isFolder} />
      </WikiListMainContent>
    </div>
  );
});

export default WikiPage;
