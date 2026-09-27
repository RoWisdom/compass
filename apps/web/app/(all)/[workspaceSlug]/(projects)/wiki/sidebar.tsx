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
import { PageFormModal } from "@/components/pages/wiki/page-form-modal";
import { SidebarWrapper } from "@/components/sidebar/sidebar-wrapper";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useUserPermissions } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";
// services
import { isPredefinedCollectionKey } from "@/services/page";
import type { TPageCollection, TPageCreateTarget, TPredefinedCollectionKey } from "@/services/page";

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
  const [isPageFormOpen, setIsPageFormOpen] = useState(false);

  // 权限口径沿用 wiki-list-main-content.tsx:39-42 的同一谓词：写端点不含 GUEST。
  const canManageCollections = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.WORKSPACE
  );

  // 新建页面的入口复用集合写入口**同一个谓词**（写端点只给 ADMIN/MEMBER），
  // 再加一层分区过滤：`archived` 下**不显示** —— 新建的页面不可能是归档的
  // （归档由 `archived_at` 决定，而它在 `resolve_collection_key` 里优先级最高）。
  //
  // **与顶栏收录按钮的 gating 有意分叉**：顶栏用 `canIncludeIntoCollection` 把
  // `private` / `archived` 都排除（`wiki/header.tsx:52`），因为把一个**已存在**的页面
  // "收录"进 Private 没有意义；而**新建**一个私有页在 Private 视图下是自然动作。
  // 这条差异是有意的，别"修"成一致。
  const canCreatePage = canManageCollections && activeCollection !== "archived";

  /**
   * 新页面落到**当前分区**（设计 §3.2d）。推导只在这里做一次，弹窗只负责把结果发出去。
   *
   * `private` 只能靠 `access=1` 表达：「私有」是 `resolve_collection_key` 里优先级高于
   * `collection_id` 的**派生**分区，库里根本没有一行叫 private 的集合可传。
   * 另外两个预置分区到不了这里：`archived` 已把按钮藏掉，`shared` 在
   * `resolve_collection_key` 里**永不返回**。
   */
  const pageTarget: TPageCreateTarget =
    activeCollection === "private"
      ? { collection_id: null, access: 1 }
      : {
          // `general`（以及任何预置键）→ 不指定集合；自建集合 → 传它自己的 uuid。
          // 用现成的 `isPredefinedCollectionKey` 而不是手写 `=== "general"`：
          // 预置键的定义只有一处，加第五个分区时这里不用改。
          collection_id: isPredefinedCollectionKey(activeCollection) ? null : activeCollection,
          access: 0,
        };

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
   * 侧栏顶部的 `＋ New page`。
   *
   * 落点是 `SidebarWrapper` 的 **`quickActions` 插槽**（`sidebar-wrapper.tsx:26,:72`）——
   * 正是为这种"标题下面一行快捷动作"准备的。但它是**共享**插槽，不是本页独占：
   * Projects 侧栏已经在用它（`(projects)/sidebar.tsx` 的
   * `quickActions={<SidebarQuickActions />}`），所以在 `sidebar-wrapper.tsx` 里改这个
   * 槽位的样式或位置，会**同时**改掉 Projects 侧栏 —— 要动它就得两头一起看。
   *
   * 隐藏（**不渲染**）而不是 disabled：一个不解释原因的灰按钮和没有入口一样糟，
   * 与侧栏集合组标题里那个 `＋`（打开 `CollectionFormModal`、由
   * `canManageCollections` 门控）和顶栏收录按钮同一条口径。
   */
  const newPageAction = canCreatePage ? (
    <button
      type="button"
      onClick={() => setIsPageFormOpen(true)}
      className="flex w-full items-center gap-2 rounded-md border-[0.5px] border-subtle px-2 py-1.5 text-13 text-secondary hover:bg-layer-1/50"
    >
      <Plus className="h-3.5 w-3.5" />
      {t("wiki_collections.menu.create_new_page")}
    </button>
  ) : undefined;

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
    <SidebarWrapper title="Wiki" quickActions={newPageAction}>
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

      <PageFormModal isOpen={isPageFormOpen} handleClose={() => setIsPageFormOpen(false)} target={pageTarget} />
    </SidebarWrapper>
  );
});
