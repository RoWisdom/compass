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
// hooks
import { usePlatformOS } from "@/hooks/use-platform-os";
import { EPageStoreType, usePage } from "@/hooks/store";

type Props = {
  pageId: string;
};

/**
 * 文件夹列表视图里的一行**子文件夹**（执行期裁定 11）。
 *
 * 与 `PageListBlock` 用**同一个底层原语**（`ListItem`），但**不是**同一个组件 ——
 * 那个组件的内容是「一个 Page」：它读 `logo_props`、拿 `getRedirectionLink()`、
 * 挂 `BlockItemAction`（那颗 `⋯`）。文件夹这三样都不能要：
 *
 * - **落点不同** —— 页面进编辑器（`/wiki/<id>`），文件夹进**它自己的列表视图**
 *   （`?folder=<id>`），也就是当前这个视图多下钻一层。`getRedirectionLink()` 给不出后者；
 * - **没有 `⋯`**（裁定 11 + F8）—— 那个菜单里两个动作（移到集合 / 移出 Wiki）都是以
 *   「页面自己有一行 `collection_id`、有一份 vault 镜像」为前提定义的，对文件夹要么
 *   无意义、要么语义未定。本轮**不给**文件夹行这类动作，而不是先随便给一个。
 *   不传 `actionableItems` 即可（`ListItem` 的这个 prop 是可选的，
 *   `core/components/core/list/list-item.tsx:23`）。
 *
 * 图标用 `lucide-react` 的 `Folder` —— 与侧栏文件夹行**同一个图标**
 * （裁定 9 的同源要求：同一棵树、两种视图里同一个东西要长得一样）。
 */
export const FolderListRow = observer(function FolderListRow(props: Props) {
  const { pageId } = props;
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
      isMobile={isMobile}
      parentRef={parentRef}
    />
  );
});
