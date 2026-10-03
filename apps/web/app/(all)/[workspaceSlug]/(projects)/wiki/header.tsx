/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
import { observer } from "mobx-react";
import { useParams, useSearchParams } from "next/navigation";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { PageIcon } from "@plane/propel/icons";
import { Header } from "@plane/ui";
import { getPageName } from "@plane/utils";
// helpers
import { BreadcrumbLink } from "@/components/common/breadcrumb-link";
import { useWikiIncludeModal } from "@/components/pages/wiki/wiki-include-modal-context";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUserPermissions } from "@/hooks/store/user";
// services
import { canIncludeIntoCollection } from "@/services/page";

export const WikiHeader = observer(function WikiHeader() {
  // router
  const { workspaceSlug } = useParams();
  const searchParams = useSearchParams();
  // 与侧栏、与 `wiki/page.tsx` 用同一条分界：**两个参数都没写 = 首页**（执行期裁定 1）。
  // `explicitCollection` / `explicitFolder` 各留一份，因为下面判的是「在不在首页」
  // 与「在不在文件夹」；`activeCollection` 则继续给分区语义用（预置键判定、能否收录），
  // 它在文件夹/首页上兜底成 `general` 只是为了让类型与既有分支不变，
  // **不再代表用户在哪儿**。
  const explicitCollection = searchParams.get("collection");
  const explicitFolder = searchParams.get("folder");
  const activeCollection = explicitCollection ?? "general";
  const isHome = explicitCollection === null && explicitFolder === null;
  /** 文件夹视图：URL 上写了 `?folder=`（执行期裁定 2 的那条判据，也传给列表侧）。 */
  const isFolder = explicitFolder !== null;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { currentWorkspace } = useWorkspace();
  const { predefined, collections, getPageById, pageParentIds, treeRows } = usePageStore(EPageStoreType.WORKSPACE);
  const { allowPermissions } = useUserPermissions();
  // 收录弹窗归 `WikiIncludeModalProvider` 所有（挂在 `wiki/layout.tsx`）。
  // 这个按钮是**常驻**入口：列表的空态 CTA 只在分区为空时出现，分区一有页面就永久消失，
  // 收录在 UI 上就再也够不着了。
  const { open: openIncludeModal } = useWikiIncludeModal();

  // 两层门控，都要过：
  //
  // 1. **分区能不能收** —— 只有 `general` 与自建集合能接收收录（`private`/`shared`/`archived`
  //    是派生分区，页面落在哪儿由 access/archived_at 决定，不由 collection_id 决定 —— 见
  //    `canIncludeIntoCollection`）。
  // 2. **工作区角色** —— 与空态 CTA 用的是**同一个谓词**（`wiki-list-main-content.tsx` 的
  //    `canIncludePages`）。写端点只给 ADMIN/MEMBER（`apps/api/plane/app/views/page/
  //    collection.py` 的 create/partial_update/destroy），这是**用户 2026-09-27 的裁定**，
  //    有意收窄设计与计划原文里的 `[ADMIN, MEMBER, GUEST]`。后端收窄了而前端不收，
  //    就会留下一个「看得见、点得动、一点就 403」的按钮。
  //
  // 两层都用**不渲染**表达，而不是渲染成 disabled：一个不解释原因的灰按钮和没有入口一样糟。
  const canIncludeHere = canIncludeIntoCollection(activeCollection);
  const canIncludePages = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.WORKSPACE
  );

  // 分区显示名：预置分区走 i18n；自定义集合走 store 里的 name；
  // 集合列表还没拉回来时退回 `fallback_name`（「集合」），不显示裸 uuid。
  // 这里**不重复发请求** —— 侧栏已经 fetchCollections，mobx 会让本组件跟着重渲染。
  //
  // Round D 起抽成具名函数：文件夹视图也要按**它所属的分区**取名（下方
  // `folderBreadcrumb`），两处共用一份推导，而不是各写一遍再漂。
  const labelForPartition = (key: string) =>
    predefined.some((item) => item.key === key)
      ? t(`wiki_collections.predefined.${key}`)
      : (collections.find((collection) => collection.id === key)?.name ?? t("wiki_collections.fallback_name"));

  const collectionLabel = labelForPartition(activeCollection);

  /**
   * 文件夹视图的面包屑尾部：`<集合名> / <祖先文件夹…> / <这个文件夹>`（设计 §10 第 4 条）。
   *
   * 三样数据都在 store 里，**不发请求**（与 `collectionLabel` 同一条纪律）：
   * - 这个文件夹**属于哪个分区** —— `treeRows` 里同 id 那一行的 `collectionKey`，
   *   与侧栏 `partitionKeyByPageId` 是同一份数据；
   * - **祖先链** —— `pageParentIds`（树里每一行都记了，文件夹同理），从自己往上走；
   * - **名字** —— `getPageById` + `getPageName`，与侧栏同一处取名字（空名 → "Untitled"）。
   *
   * **必须截断**：`BreadcrumbLink` 的标签容器是 `max-w-[150px]` + `truncate`
   * （`components/common/breadcrumb-link.tsx` 的 `LabelWrapper`），而 `truncate`
   * 从**尾部**截 —— 段数一多，被丢掉的恰好是"用户现在在哪儿"那一段。所以只保留
   * **最后 3 段**，前面用 `…` 顶替。这不是"以后再优化"：不这么做，
   * 深层文件夹的面包屑**必然是错的**（会显示成祖先的名字）。
   */
  const folderBreadcrumb = useMemo(() => {
    if (!explicitFolder) return "";
    const partitionKey = treeRows.find((row) => row.pageId === explicitFolder)?.collectionKey;
    const partitionLabel = partitionKey ? labelForPartition(partitionKey) : t("wiki_collections.fallback_name");

    // 从自己往上走到根。**封顶防环**：`Page.parent` 理论上不成环，但一个循环不该靠
    // "理论上"保证自己会停 —— 真成环就是一次把浏览器转死的渲染。
    const chain: string[] = [];
    let cursor: string | null | undefined = explicitFolder;
    for (let depth = 0; cursor && depth < 32; depth += 1) {
      const node = getPageById(cursor);
      // 取不到就**停**：`getPageName(undefined)` 返回的是**空串**
      // （`packages/utils/src/page.ts:95`，不是 "Untitled"），继续走会拼出
      // `" / "` 这种空洞。宁可少一段。
      if (!node) break;
      chain.unshift(getPageName(node.name));
      cursor = pageParentIds[cursor] ?? null;
    }

    const segments = [partitionLabel, ...chain];
    if (segments.length <= 3) return segments.join(" / ");
    return ["…", ...segments.slice(-3)].join(" / ");
    // `labelForPartition` 与 `t` 都不进依赖：前者是每次渲染新建的闭包（放进来等于
    // 每渲染必重算），它读的 `predefined` / `collections` 已经在下面了。
    // oxlint-disable-next-line exhaustive-deps
  }, [explicitFolder, getPageById, pageParentIds, treeRows, predefined, collections, t]);

  return (
    <Header>
      <Header.LeftItem>
        <BreadcrumbLink
          label={`${currentWorkspace?.name ?? ""} / ${
            isHome ? t("wiki_home.title") : isFolder ? folderBreadcrumb : collectionLabel
          }`}
          href={`/${workspaceSlug}/wiki/`}
          icon={<PageIcon className="h-4 w-4 text-tertiary" />}
          isLast
        />
      </Header.LeftItem>

      <Header.RightItem>
        {/*
          **首页上不渲染这颗按钮。** 它做的事是「把已有页面收录进当前分区」，
          而首页**没有当前分区** —— `activeCollection` 在这里只是 `general` 的兜底，
          点下去会把页面收录进「常规」，与用户以为所在的「首页」不是一回事。
          不渲染（而不是 disabled）与下面 `canIncludeHere` 那条的既有理由一致：
          一个不解释原因的灰按钮和没有入口一样糟。

          **Round D：文件夹视图里也不渲染**（执行期裁定 2）。设计 §5.3 写「收窄到**这个
          文件夹**」，但那**表示不出来**：这个按钮调的是「收录已有页面」端点，请求体只有
          `page_ids` + `collection_id`，而 `collection_id` 只接受 `PageCollection` 的 uuid ——
          传文件夹 uuid 落 404。文件夹里建页走的是侧栏那颗 `＋` 的「新建页面」
          （`create_page` 端点，吃 `parent`），不是这个按钮。
        */}
        {/* 文案复用 `header.add_page`（弹窗标题用的同一个键，本来就是给顶栏准备的） */}
        {!isHome && !isFolder && canIncludeHere && canIncludePages && (
          <Button variant="primary" size="lg" onClick={openIncludeModal}>
            {t("wiki_collections.header.add_page")}
          </Button>
        )}
      </Header.RightItem>
    </Header>
  );
});
