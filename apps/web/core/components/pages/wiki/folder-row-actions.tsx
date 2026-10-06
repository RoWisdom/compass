/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type * as React from "react";
import { useState } from "react";
import { observer } from "mobx-react";
import { FileOutput, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { IconButton } from "@plane/propel/icon-button";
import { CustomMenu } from "@plane/ui";
// components
import { DeleteFolderModal } from "@/components/pages/wiki/delete-folder-modal";
import { RenameFolderModal } from "@/components/pages/wiki/rename-folder-modal";
// hooks
import type { EPageStoreType } from "@/hooks/store";

type Props = {
  folderId: string;
  /** 这个文件夹属于哪一棵树。**必填、不给默认值**：默认成 WORKSPACE 会让项目侧
   *  漏传时静默地去改 wiki 的那棵树 —— 一个不会报错的错。 */
  storeType: EPageStoreType;
  /**
   * 「移动到…」的目标选择器。**由调用方给**：wiki 那一个绑死「集合分区」这一层
   * （读 `collections` / `treeRows`），而项目侧没有集合这一层，两者无法共用一个组件。
   * 收组件类型而不是 render prop：少一层闭包，调用点也直白。
   */
  moveToModal: React.ComponentType<{
    isOpen: boolean;
    pageId: string | null;
    onMoved: () => void;
    handleClose: () => void;
  }>;
  /** 删除/移动成功后重拉**当前视图**。 */
  onChanged: () => void;
};

/**
 * 文件夹行的 `•••`（Round E）。**侧栏与列表视图共用这一个组件** ——
 * Round D 的同源要求：同一棵树在两个视图里，同一个东西要长得一样，
 * 而且只有**一份**实现（分叉出去的那一份迟早会漂）。
 *
 * 三个动作对文件夹都成立：
 *   · 「移动到…」—— 位置选择器，文件夹与页面走同一套（后端 `parent` 对两者同一条路径）；
 *   · 「重命名」—— 只改 `name`（Round F），vault 目录跟着改名、子项整棵随行；
 *   · 「删除」—— Round I 起是**连里面的页面与子文件夹一起删**（设计 §4.1，
 *     与「移出 Wiki」的取消收录语义**刻意不同**，理由见 `collection.py` 的 `destroy`）。
 *
 * 页面行**不用**这个组件 —— 它的动作是「移动到… / 移出 Wiki」，由
 * `WikiListRoot.buildRowActions` 构建。两套动作不重合，所以是两处构建，
 * 而不是一个带 `isFolder` 分支的大组件。
 */
export const FolderRowActions = observer(function FolderRowActions(props: Props) {
  const { folderId, storeType, moveToModal: MoveToModalComponent, onChanged } = props;
  // plane hooks
  const { t } = useTranslation();
  // states
  const [isMoveOpen, setIsMoveOpen] = useState(false);
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [isRenameOpen, setIsRenameOpen] = useState(false);

  return (
    <>
      {/* 文件夹不是集合 —— 用「集合选项」会念错对象；`common.options` 是通用键，
          也省掉为一条 aria-label 去动 19 个语言文件。 */}
      <CustomMenu
        customButton={<IconButton icon={MoreHorizontal} variant="ghost" size="sm" />}
        ariaLabel={t("common.options")}
        closeOnSelect
      >
        <CustomMenu.MenuItem onClick={() => setIsMoveOpen(true)}>
          <span className="flex items-center gap-2">
            <FileOutput className="size-3" />
            {t("wiki_collections.menu.move_to")}
          </span>
        </CustomMenu.MenuItem>
        <CustomMenu.MenuItem onClick={() => setIsRenameOpen(true)}>
          <span className="flex items-center gap-2">
            <Pencil className="size-3" />
            {t("wiki_collections.menu.rename")}
          </span>
        </CustomMenu.MenuItem>
        <CustomMenu.MenuItem onClick={() => setIsDeleteOpen(true)}>
          <span className="flex items-center gap-2">
            <Trash2 className="size-3" />
            {t("common.delete")}
          </span>
        </CustomMenu.MenuItem>
      </CustomMenu>
      <MoveToModalComponent
        isOpen={isMoveOpen}
        pageId={folderId}
        onMoved={onChanged}
        handleClose={() => setIsMoveOpen(false)}
      />
      <DeleteFolderModal
        isOpen={isDeleteOpen}
        folderId={folderId}
        storeType={storeType}
        onDeleted={onChanged}
        handleClose={() => setIsDeleteOpen(false)}
      />
      <RenameFolderModal
        isOpen={isRenameOpen}
        folderId={folderId}
        storeType={storeType}
        onRenamed={onChanged}
        handleClose={() => setIsRenameOpen(false)}
      />
    </>
  );
});
