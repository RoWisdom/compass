/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useRef } from "react";
import { observer } from "mobx-react";
import { Logo } from "@plane/propel/emoji-icon-picker";
import { PageIcon } from "@plane/propel/icons";
// plane imports
import type { TContextMenuItem } from "@plane/ui";
import { getPageName } from "@plane/utils";
// components
import { ListItem } from "@/components/core/list";
import type { TPageActions } from "@/components/pages/dropdowns";
import { BlockItemAction } from "@/components/pages/list/block-item-action";
// hooks
import { usePlatformOS } from "@/hooks/use-platform-os";
// plane web hooks
import type { EPageStoreType } from "@/hooks/store";
import { usePage } from "@/hooks/store";

type TPageListBlock = {
  pageId: string;
  storeType: EPageStoreType;
  /**
   * 追加到行操作菜单**末尾**的菜单项。只有工作区（Wiki）侧会传 —— 见 `WikiListRoot`。
   *
   * 项目页不传这个 prop，于是 `optionsOrder` 与加它之前逐字一致（`block-item-action.tsx`
   * 里的 `BASE_OPTIONS_ORDER` + 空数组），PROJECT 的行为不受影响。
   * 选这个形状（外部传入）而不是在 `PageListBlock` 里按 `storeType` 分支，理由见报告。
   */
  extraActions?: (TContextMenuItem & { key: TPageActions })[];
};

export const PageListBlock = observer(function PageListBlock(props: TPageListBlock) {
  const { pageId, storeType, extraActions } = props;
  // refs
  const parentRef = useRef(null);
  // hooks
  const page = usePage({
    pageId,
    storeType,
  });
  const { isMobile } = usePlatformOS();
  // handle page check
  if (!page) return null;
  // derived values
  const { name, logo_props, getRedirectionLink } = page;

  return (
    <ListItem
      prependTitleElement={
        <>
          {logo_props?.in_use ? (
            <Logo logo={logo_props} size={16} type="lucide" />
          ) : (
            <PageIcon className="h-4 w-4 text-tertiary" />
          )}
        </>
      }
      title={getPageName(name)}
      itemLink={getRedirectionLink()}
      actionableItems={
        <BlockItemAction page={page} parentRef={parentRef} storeType={storeType} extraOptions={extraActions} />
      }
      isMobile={isMobile}
      parentRef={parentRef}
    />
  );
});
