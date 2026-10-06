/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useRef } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { ChevronRightIcon, Folder } from "lucide-react";
// plane imports
import { cn, getPageName } from "@plane/utils";
// components
import { ListItem } from "@/components/core/list";
import { ProjectMoveToModal } from "@/components/pages/project-move-to-modal";
import { FolderRowActions } from "@/components/pages/wiki/folder-row-actions";
// hooks
import { usePlatformOS } from "@/hooks/use-platform-os";
import { EPageStoreType, usePage } from "@/hooks/store";

type Props = {
  pageId: string;
  /** 这一行现在是不是展开的（展开集里有它）。 */
  isExpanded: boolean;
  /**
   * 有没有**当前可见**的子项。没有就不渲染箭头 —— 一个点下去什么都不发生的箭头
   * 比没有箭头更坏。判据由调用方给（它手里有过滤后的 id 集）。
   */
  hasChildren: boolean;
  onToggle: () => void;
  /**
   * 行右那颗 `•••` 渲不渲染。**由 `PagesListRoot` 的 `canWrite` 给**（工作区之外的
   * 项目级 ADMIN/MEMBER）—— 菜单里的动作都是写，不收窄的话 GUEST 会看见一个点下去
   * 必然 403 的 `•••`。与同列表里页面行的 `extraActions` 是同一道门。
   *
   * 本任务（Round J Task 7）**只声明、不渲染**：`•••`（移动到… / 重命名 / 删除）
   * 由 Task 10 接上。此处先不打散它，是为了让 Task 10 的改动只有一个落点。
   */
  canWrite: boolean;
  /** 删除/移动成功后重拉**当前项目的整棵树** —— 由 `PagesListRoot` 给。Task 10 接上。 */
  onChanged: () => void;
};

/**
 * 项目 Pages 列表里的一行**文件夹**（罗盘 Round J）。
 *
 * 与 wiki 的 `folder-list-row.tsx` **不能共用**：那一行是**链接**（点进
 * `?folder=<id>` 那个下钻视图），项目侧没有这个落点 —— 项目的列表只有一层。
 * 所以这里 `disableLink`，整行不可导航，只有箭头可点。
 *
 * 与 `PageListBlock` 共用底层原语（`ListItem`），所以行高/悬停/边框与同列表里的
 * 页面行一致；图标用 `Folder`，与 wiki 侧同一个图标（裁定 A「同一棵树」的同源要求）。
 */
export const ProjectFolderListRow = observer(function ProjectFolderListRow(props: Props) {
  const { pageId, isExpanded, hasChildren, onToggle, canWrite, onChanged } = props;
  // router
  const { workspaceSlug, projectId } = useParams();
  // refs —— 与 `PageListBlock` 同款：`ListItem` 的 `parentRef` 是行内浮层定位用的。
  const parentRef = useRef(null);
  // hooks
  const { isMobile } = usePlatformOS();
  const page = usePage({ pageId, storeType: EPageStoreType.PROJECT });

  // 首帧 / 树还没回来时取不到 —— 渲染 `null`，与 `PageListBlock` / wiki 的
  // `FolderListRow` 逐字同款（这一帧过去会自愈，`usePage` 的宿主是 observer）。
  if (!page) return null;

  return (
    <ListItem
      // `itemLink` 是 `ListItem` 的**必填** prop，但这一行不是链接（见组件 docblock）：
      // `disableLink` 让 `ControlLink` 走 `disabled` 分支，渲出一个**没有 href、
      // 没有 onClick** 的 `<a>`，点行什么都不发生。这个值因此从不参与导航。
      itemLink={`/${workspaceSlug}/projects/${projectId}/pages/`}
      disableLink
      prependTitleElement={<Folder className="h-4 w-4 flex-shrink-0 text-tertiary" />}
      title={getPageName(page.name)}
      // 折叠箭头放 `quickActionElement`，**不是** `prependTitleElement`：后者渲在
      // `ControlLink`（那个 `<a>`）**里面**，往里塞 `<button>` 是「交互内容嵌交互内容」，
      // HTML 无效。`quickActionElement` 是 `ControlLink` 的**兄弟**（`list-item.tsx`
      // 里它渲在 `</ControlLink>` 之后），合法。代价是箭头落在标题右端而非左侧图标旁
      // —— 这是为合法性付的价，认。
      quickActionElement={
        hasChildren ? (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={isExpanded}
            aria-label={getPageName(page.name)}
            className="flex-shrink-0 p-1 text-tertiary"
          >
            <ChevronRightIcon className={cn("size-3 transition-transform", { "rotate-90": isExpanded })} />
          </button>
        ) : (
          // 没有子项就不给箭头，留**等宽占位**保对齐：按钮是 `p-1` + `size-3` = 20px，
          // 占位用 `size-5`（与 wiki `move-to-modal.tsx` 的「无子行」占位同款）。
          <span className="size-5 flex-shrink-0" />
        )
      }
      actionableItems={
        canWrite ? (
          <FolderRowActions
            folderId={pageId}
            storeType={EPageStoreType.PROJECT}
            moveToModal={ProjectMoveToModal}
            onChanged={onChanged}
          />
        ) : undefined
      }
      isMobile={isMobile}
      parentRef={parentRef}
    />
  );
});
