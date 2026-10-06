/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useRef } from "react";
import { observer } from "mobx-react";
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

/**
 * 行左那一格：**图标与箭头同格互换**用的固定槽。`grid` + `col-start-1 row-start-1`
 * 让两个图标叠在同一个位置，靠透明度互换。照抄 wiki 侧栏的 `TREE_LEAD_SLOT_CLASS`。
 */
const LEAD_SLOT_CLASS = "grid size-4 shrink-0 place-items-center";

type Props = {
  pageId: string;
  /** 点这一行去哪（由 `PagesListRoot` 造 —— 它才知道当前的 `?type=`）。 */
  itemLink: string;
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
 * 与 wiki 的 `folder-list-row.tsx` **不能共用**：那一行的落点写死在自己的文件里
 * （wiki 的 `?folder=<id>` 下钻视图），而项目侧要先知道当前的 `?type=` 才能把链接
 * 造对 —— 所以链接当 **prop** 收进来，由 `PagesListRoot` 造。**两条路并存**：
 * 点行主体**下钻**（`itemLink`），点图标**原地展开/收起**（`prependActionElement`）。
 *
 * 与 `PageListBlock` 共用底层原语（`ListItem`），所以行高/悬停/边框与同列表里的
 * 页面行一致；图标用 `Folder`，与 wiki 侧同一个图标（裁定 A「同一棵树」的同源要求）。
 */
export const ProjectFolderListRow = observer(function ProjectFolderListRow(props: Props) {
  const { pageId, itemLink, isExpanded, hasChildren, onToggle, canWrite, onChanged } = props;
  // refs —— 与 `PageListBlock` 同款：`ListItem` 的 `parentRef` 是行内浮层定位用的。
  const parentRef = useRef(null);
  // hooks
  const { isMobile } = usePlatformOS();
  const page = usePage({ pageId, storeType: EPageStoreType.PROJECT });

  // 首帧 / 树还没回来时取不到 —— 渲染 `null`，与 `PageListBlock` / wiki 的
  // `FolderListRow` 逐字同款（这一帧过去会自愈，`usePage` 的宿主是 observer）。
  if (!page) return null;

  const title = getPageName(page.name);

  return (
    <ListItem
      // 点行主体 → 下钻进这个文件夹（链接由 `PagesListRoot` 造，见组件 docblock）。
      itemLink={itemLink}
      // 图标与箭头同格互换，**放在链接之外**这一格（`prependActionElement`）：
      //
      // * 箭头是 `<button>`，而 `prependTitleElement` 渲在 `ControlLink`（那个 `<a>`）
      //   **里面** —— 往里塞按钮就是「交互内容嵌交互内容」，HTML 非法。`prependActionElement`
      //   是 `ControlLink` 的**兄弟**，合法。
      // * 图标搬出链接还有个理由：要让图标与箭头在**同一格**里换，图标得先站进那一格
      //   （原来它在链接里，箭头在链接外，两处换不了）。手法照抄 wiki 侧栏 `renderRow`。
      //
      // `mr-1` 是**对齐补偿**：图标原来在链接里、靠链接的 `gap-4`（16px）与标题相隔；
      // 现在它站在链接外、与链接之间只有行容器的 `gap-3`（12px），补回 4px 才仍是 16px，
      // 文件夹行与同列表里的页面行（`block.tsx`，图标同样在链接里）标题仍齐平。
      prependActionElement={
        hasChildren ? (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={isExpanded}
            aria-label={title}
            className={cn(LEAD_SLOT_CLASS, "mr-1 rounded-sm text-tertiary hover:text-secondary")}
          >
            {/* 两个图标**都不写颜色**：颜色由按钮那格给（静止 `text-tertiary`、
                悬停 `text-secondary`），免得两处各写一份迟早对不上。 */}
            <Folder className="col-start-1 row-start-1 h-4 w-4 transition-opacity group-focus-within:opacity-0 group-hover:opacity-0" />
            <ChevronRightIcon
              className={cn(
                "col-start-1 row-start-1 size-3 opacity-0 transition group-focus-within:opacity-100 group-hover:opacity-100",
                { "rotate-90": isExpanded }
              )}
            />
          </button>
        ) : (
          // 没有子项就没有可换的箭头，但仍占**同一格**（同一套 `LEAD_SLOT_CLASS` + `mr-1`），
          // 否则折叠行与可折叠行的标题会差 4px。
          <span className={cn(LEAD_SLOT_CLASS, "mr-1")}>
            <Folder className="h-4 w-4 text-tertiary" />
          </span>
        )
      }
      title={title}
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
