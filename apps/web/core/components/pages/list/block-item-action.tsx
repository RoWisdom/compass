/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { Earth, Info, Minus } from "lucide-react";
// plane imports
import { LockIcon } from "@plane/propel/icons";
import { Tooltip } from "@plane/propel/tooltip";
import type { TContextMenuItem } from "@plane/ui";
import { Avatar, FavoriteStar } from "@plane/ui";
import { renderFormattedDate, getFileURL } from "@plane/utils";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { usePageOperations } from "@/hooks/use-page-operations";
// plane web hooks
import type { EPageStoreType } from "@/hooks/store";
// store
import type { TPageInstance } from "@/store/pages/base-page";
// local imports
import type { TPageActions } from "../dropdowns";
import { PageActions } from "../dropdowns";

/**
 * 项目页与 Wiki 共用的行菜单项顺序。
 * 工作区专属的动作（`extraOptions`，目前只有 Wiki 侧会传）**追加在后面** ——
 * 见 `PageActions` 的 `arrangedOptions`：顺序完全由 `optionsOrder` 决定。
 */
const BASE_OPTIONS_ORDER: TPageActions[] = [
  "open-in-new-tab",
  "copy-link",
  "make-a-copy",
  "toggle-lock",
  "toggle-access",
  "archive-restore",
  "delete",
];

type Props = {
  page: TPageInstance;
  parentRef: React.RefObject<HTMLElement>;
  storeType: EPageStoreType;
  /** 见 `block.tsx` 的同名 prop。项目页不传。 */
  extraOptions?: (TContextMenuItem & { key: TPageActions })[];
};

export const BlockItemAction = observer(function BlockItemAction(props: Props) {
  const { page, parentRef, storeType, extraOptions } = props;
  // store hooks
  const { getUserDetails } = useMember();
  // page operations
  const { pageOperations } = usePageOperations({
    page,
  });
  // derived values
  const { access, created_at, is_favorite, owned_by, canCurrentUserFavoritePage } = page;
  const ownerDetails = owned_by ? getUserDetails(owned_by) : undefined;

  return (
    <>
      {/* page details */}
      <div className="cursor-default">
        <Tooltip tooltipHeading="Owned by" tooltipContent={ownerDetails?.display_name}>
          <Avatar src={getFileURL(ownerDetails?.avatar_url ?? "")} name={ownerDetails?.display_name} />
        </Tooltip>
      </div>
      <div className="cursor-default text-tertiary">
        <Tooltip tooltipContent={access === 0 ? "Public" : "Private"}>
          {access === 0 ? <Earth className="h-4 w-4" /> : <LockIcon className="h-4 w-4" />}
        </Tooltip>
      </div>
      {/* vertical divider */}
      <Minus className="-mx-3 h-5 w-5 rotate-90 text-placeholder" strokeWidth={1} />

      {/* page info */}
      <Tooltip tooltipContent={`Created on ${renderFormattedDate(created_at)}`}>
        <span className="grid h-4 w-4 cursor-default place-items-center">
          <Info className="h-4 w-4 text-tertiary" />
        </span>
      </Tooltip>

      {/* favorite/unfavorite */}
      {canCurrentUserFavoritePage && (
        <FavoriteStar
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            pageOperations.toggleFavorite();
          }}
          selected={is_favorite}
        />
      )}

      {/* quick actions dropdown */}
      {/* `extraOptions` 的 key 必须进 `optionsOrder`，否则 `PageActions` 只把它们塞进 MENU_ITEMS
          却不会渲染（`arrangedOptions` 是 `optionsOrder.map(...)`）。这里统一追加在末尾。 */}
      <PageActions
        optionsOrder={[...BASE_OPTIONS_ORDER, ...(extraOptions?.map((option) => option.key) ?? [])]}
        extraOptions={extraOptions}
        page={page}
        parentRef={parentRef}
        storeType={storeType}
      />
    </>
  );
});
