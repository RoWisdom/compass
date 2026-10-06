/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useRef } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { Folder } from "lucide-react";
// plane imports
import { getPageName } from "@plane/utils";
// components
import { ListItem } from "@/components/core/list";
import { FolderRowActions } from "@/components/pages/wiki/folder-row-actions";
import { MoveToModal } from "@/components/pages/wiki/move-to-modal";
// hooks
import { usePlatformOS } from "@/hooks/use-platform-os";
import { EPageStoreType, usePage } from "@/hooks/store";

type Props = {
  pageId: string;
  /**
   * 行右那颗 `•••` 渲不渲染。**由 `WikiListRoot` 的 `canWriteWiki` 给** ——
   * 菜单里三个动作都是写（移动到… / 重命名 / 删除），后端只给 ADMIN/MEMBER
   * （都走 `wiki-pages/` 的 PATCH 或 DELETE）；不收窄的话 GUEST 会看见一个点下去
   * 必然 403 的 `•••`。与同一视图里页面行的 `extraActions` 是同一道门。
   */
  canWrite: boolean;
  /** 删除/移动成功后重拉**当前视图** —— 由 `WikiListRoot` 给。 */
  onChanged: () => void;
};

/**
 * 文件夹列表视图里的一行**子文件夹**（Round E 起带 `•••`）。
 *
 * 与 `PageListBlock` 用**同一个底层原语**（`ListItem`），但**不是**同一个组件 ——
 * 那个组件的内容是「一个 Page」：它读 `logo_props`、拿 `getRedirectionLink()`、
 * 挂 `BlockItemAction`（那颗 `⋯`）。文件夹这三样都不能要：
 *
 * - **落点不同** —— 页面进编辑器（`/wiki/<id>`），文件夹进**它自己的列表视图**
 *   （`?folder=<id>`），也就是当前这个视图多下钻一层。`getRedirectionLink()` 给不出后者；
 * - **有 `⋯`**（Round E 起，裁定 11 作废）—— 当时不给，是因为那两个动作都是**页面专属**的
 *   （移到集合 / 移出 Wiki，都以「页面自己有一行 `collection_id`、有一份 vault 镜像」为前提），
 *   对文件夹「语义未定」。Round E 把文件夹的语义定死了（Confluence 的 Folders 模型）：
 *   移动到… / 删除（Round F 起再加「重命名」，见 `rename-folder-modal.tsx`）。
 *   所以现在给，而且与侧栏那处**共用** `FolderRowActions`。
 *
 * 图标用 `lucide-react` 的 `Folder` —— 与侧栏文件夹行**同一个图标**
 * （裁定 9 的同源要求：同一棵树、两种视图里同一个东西要长得一样）。
 */
export const FolderListRow = observer(function FolderListRow(props: Props) {
  const { pageId, canWrite, onChanged } = props;
  // router
  const { workspaceSlug } = useParams();
  // refs —— 与 `PageListBlock` 同款：`ListItem` 的 `parentRef` 是行内浮层定位用的。
  const parentRef = useRef(null);
  // hooks
  const { isMobile } = usePlatformOS();
  const page = usePage({ pageId, storeType: EPageStoreType.WORKSPACE });

  // 首帧 / 树还没回来时取不到 —— **渲染 `null`**，与 `PageListBlock:48` 逐字同款
  // （它也是 `if (!page) return null`）。这一帧过去会自愈（`usePage` 是 observer 的）。
  if (!page) return null;

  return (
    <ListItem
      prependTitleElement={<Folder className="h-4 w-4 flex-shrink-0 text-tertiary" />}
      title={getPageName(page.name)}
      itemLink={`/${workspaceSlug}/wiki/?folder=${pageId}`}
      actionableItems={
        canWrite ? (
          <FolderRowActions
            folderId={pageId}
            storeType={EPageStoreType.WORKSPACE}
            moveToModal={MoveToModal}
            onChanged={onChanged}
          />
        ) : undefined
      }
      isMobile={isMobile}
      parentRef={parentRef}
    />
  );
});
