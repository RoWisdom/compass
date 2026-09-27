/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import type { ReactNode } from "react";
import { observer } from "mobx-react";
import { useParams, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { MoreHorizontal, Plus } from "lucide-react";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { IconButton } from "@plane/propel/icon-button";
import { PageIcon } from "@plane/propel/icons";
import { CustomMenu } from "@plane/ui";
import { cn } from "@plane/utils";
// components
import { CollectionFormModal } from "@/components/pages/wiki/collection-form-modal";
import { SidebarWrapper } from "@/components/sidebar/sidebar-wrapper";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useUserPermissions } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";
// services
import type { TPageCollection, TPredefinedCollectionKey } from "@/services/page";

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
  const { allowPermissions } = useUserPermissions();
  // state
  const [isFormOpen, setIsFormOpen] = useState(false);
  /** `null` = 新建模式；有值 = 重命名这个。弹窗的两种模式由它一个变量分叉。 */
  const [editingCollection, setEditingCollection] = useState<TPageCollection | null>(null);

  // 权限口径沿用 wiki-list-main-content.tsx:39-42 的同一谓词：写端点不含 GUEST。
  const canManageCollections = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.WORKSPACE
  );

  // 集合列表（含计数）
  useSWR(
    workspaceSlug ? `WIKI_COLLECTIONS_${workspaceSlug}` : null,
    workspaceSlug ? () => fetchCollections(workspaceSlug) : null
  );

  const goTo = (key: string) => router.push(`/${workspaceSlug}/wiki/?collection=${key}`);

  /** 预置分区的计数。列表还没回来时按 0 算 —— 先把结构渲染出来，计数随后补齐。 */
  const predefinedCount = (key: string) => predefined.find((item) => item.key === key)?.page_count ?? 0;

  const openCreate = () => {
    setEditingCollection(null);
    setIsFormOpen(true);
  };

  const openEdit = (collection: TPageCollection) => {
    setEditingCollection(collection);
    setIsFormOpen(true);
  };

  /**
   * `options` 只给用户自建集合传 —— `General` 是**派生**分区（`collection_id IS NULL`），
   * 重命名它没有落点；官方那张图里 General 是真实行所以有 `⋯`，罗盘结构不同。
   * `Private` / `Archived` 同理：它们是 `access` / `archived_at` 推导出来的，不是一个可改名的对象。
   */
  const renderRow = (key: string, label: string, count: number, options?: ReactNode) => (
    <div key={key} className="flex w-full items-center gap-1">
      <button
        type="button"
        onClick={() => goTo(key)}
        className={cn(
          "flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-13",
          activeCollection === key ? "bg-layer-1 text-primary" : "text-secondary hover:bg-layer-1/50"
        )}
      >
        <span className="flex items-center gap-2 truncate">
          <PageIcon className="h-4 w-4 text-tertiary" />
          <span className="truncate">{label}</span>
        </span>
        <span className="text-11 text-tertiary">{count}</span>
      </button>
      {options}
    </div>
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
        <div className="flex w-full items-center justify-between gap-2 px-2 pt-1">
          <span className="text-11 text-tertiary">{t("wiki_collections.fallback_name")}</span>
          {/* 对 GUEST **隐藏**而不是 disabled —— 与既有口径一致，理由见
              wiki-list-main-content.tsx 的同一谓词。 */}
          {canManageCollections && (
            <button
              type="button"
              onClick={openCreate}
              aria-label={t("wiki_collections.create_modal.title")}
              className="rounded-sm p-0.5 text-tertiary hover:bg-layer-1 hover:text-secondary"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        {renderRow("general", t("wiki_collections.predefined.general"), predefinedCount("general"))}
        {collections.map((collection) =>
          renderRow(
            collection.id,
            collection.name,
            collection.page_count,
            canManageCollections ? (
              <CustomMenu
                customButton={<IconButton icon={MoreHorizontal} variant="ghost" size="sm" />}
                ariaLabel={t("wiki_collections.menu.collection_options")}
                closeOnSelect
              >
                <CustomMenu.MenuItem onClick={() => openEdit(collection)}>
                  {t("wiki_collections.menu.edit_collection")}
                </CustomMenu.MenuItem>
              </CustomMenu>
            ) : undefined
          )
        )}

        {PARTITION_ROWS.map((key) => renderRow(key, t(`wiki_collections.predefined.${key}`), predefinedCount(key)))}
      </div>

      {/*
        弹窗挂在侧栏自己身上，**不**提到 `wiki/layout.tsx` 的 provider 层：侧栏
        （`(projects)/_sidebar.tsx:71`）根本不在那个 provider 的子树里，而且它只有
        侧栏一个调用方、侧栏又是单实例，一份 state 就够。详见
        `collection-form-modal.tsx` 的 docblock。
      */}
      <CollectionFormModal
        isOpen={isFormOpen}
        collection={editingCollection}
        handleClose={() => setIsFormOpen(false)}
        onCreated={(created) => router.push(`/${workspaceSlug}/wiki/?collection=${created.id}`)}
      />
    </SidebarWrapper>
  );
});
