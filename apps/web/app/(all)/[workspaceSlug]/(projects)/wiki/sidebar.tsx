/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { useTranslation } from "@plane/i18n";
import { PageIcon } from "@plane/propel/icons";
import { cn } from "@plane/utils";
// components
import { SidebarWrapper } from "@/components/sidebar/sidebar-wrapper";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useAppRouter } from "@/hooks/use-app-router";
// services
import type { TPredefinedCollectionKey } from "@/services/page";

/**
 * 「集合」组之外的预置分区，按官方侧栏的顺序排在集合组下面。
 *
 * `shared` **不在这里** —— `resolve_collection_key` 永不返回它
 * （`apps/api/plane/utils/wiki_collections.py:37`：开源版没有「发布」字段，
 * 该分区恒空）。渲染一个永远空的分区只是噪音，这是本页对「完整对齐官方分组」
 * 唯一一处有意偏离。旧版侧栏本来就把它过滤掉了
 * （`item.key !== "shared" || item.page_count > 0`），所以这里不是行为变化。
 */
const PARTITION_ROWS: TPredefinedCollectionKey[] = ["private", "archived"];

export const WikiSidebar = observer(function WikiSidebar() {
  // router
  const router = useAppRouter();
  const { workspaceSlug } = useParams();
  const searchParams = useSearchParams();
  const activeCollection = searchParams.get("collection") ?? "general";
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { predefined, collections, fetchCollections } = usePageStore(EPageStoreType.WORKSPACE);

  // 集合列表（含计数）
  useSWR(
    workspaceSlug ? `WIKI_COLLECTIONS_${workspaceSlug}` : null,
    workspaceSlug ? () => fetchCollections(workspaceSlug) : null
  );

  const goTo = (key: string) => router.push(`/${workspaceSlug}/wiki/?collection=${key}`);

  /** 预置分区的计数。列表还没回来时按 0 算 —— 先把结构渲染出来，计数随后补齐。 */
  const predefinedCount = (key: string) => predefined.find((item) => item.key === key)?.page_count ?? 0;

  const renderRow = (key: string, label: string, count: number) => (
    <button
      key={key}
      type="button"
      onClick={() => goTo(key)}
      className={cn(
        "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-13",
        activeCollection === key ? "bg-layer-1 text-primary" : "text-secondary hover:bg-layer-1/50"
      )}
    >
      <span className="flex items-center gap-2 truncate">
        <PageIcon className="h-4 w-4 text-tertiary" />
        <span className="truncate">{label}</span>
      </span>
      <span className="text-11 text-tertiary">{count}</span>
    </button>
  );

  return (
    <SidebarWrapper title="Wiki">
      <div className="flex w-full flex-col gap-1">
        {/*
          「集合」组 = `general` 预置分区 + 全部用户自建集合。官方把 General 摆在
          集合组下，这里对齐。

          组标题借的是 `wiki_collections.fallback_name`（en "Collection" / zh「集合」）：
          i18n 里没有专给组标题的键，而新增一个键要同步 18 份 locale 文件。
          代价是这个键会同时承担两个用途（「集合没有名字时的称呼」与「组标题」）；
          日后要区分，再补 `wiki_collections.title` 并把这里换过去。
        */}
        <p className="px-2 pt-1 text-11 text-tertiary">{t("wiki_collections.fallback_name")}</p>
        {renderRow("general", t("wiki_collections.predefined.general"), predefinedCount("general"))}
        {collections.map((collection) => renderRow(collection.id, collection.name, collection.page_count))}

        {PARTITION_ROWS.map((key) => renderRow(key, t(`wiki_collections.predefined.${key}`), predefinedCount(key)))}
      </div>
    </SidebarWrapper>
  );
});
