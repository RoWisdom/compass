/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Plus } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { IconButton } from "@plane/propel/icon-button";
import type { TPageNavigationTabs } from "@plane/types";
import { CustomMenu } from "@plane/ui";
// components
import { ProjectCreateFolderModal } from "@/components/pages/project-create-folder-modal";
// hooks
import { useProjectPageCreate } from "@/hooks/use-project-page-create";

type Props = {
  pageType: TPageNavigationTabs;
  /** 新建的东西落在哪个文件夹下。`null` = 顶层。 */
  parentId?: string | null;
};

/**
 * 项目 Pages 列表的新建入口：**一个 `＋`、两种节点**。
 *
 * 形状照 wiki 侧栏的 `renderCreateMenu`（`wiki/sidebar.tsx`）—— 那边也是同一个
 * `＋` 里并列「新建页面 / 新建文件夹」，不另开一颗按钮、不另开一个对话框
 * （Confluence F7，本仓的既定形状）。
 *
 * 落点由调用方给（`parentId`）—— 在根视图是顶层（不传 / `null`），下钻进某个
 * 文件夹后就是那个文件夹。两种建法都落同一个 `parentId`。
 *
 * 建页面**直接建、直接跳**（与原来那颗「Add page」按钮逐字同行为）；建文件夹要先
 * 起名字，所以开弹窗 —— 一个空名文件夹会在用户的**真实 vault** 里造出一个以 uuid
 * 命名的目录（`_file_stem` 在名字净化成空串时回落到 id），那不能接受。
 */
export const ProjectCreateMenu = observer(function ProjectCreateMenu(props: Props) {
  const { pageType, parentId } = props;
  // plane hooks
  const { t } = useTranslation();
  // states
  const [isCreateFolderOpen, setIsCreateFolderOpen] = useState(false);
  // hooks
  const { createUntitledPage, isCreatingPage } = useProjectPageCreate(pageType, parentId);

  return (
    <>
      <CustomMenu
        customButton={<IconButton icon={Plus} variant="ghost" size="lg" loading={isCreatingPage} />}
        ariaLabel={t("add_new")}
        closeOnSelect
      >
        <CustomMenu.MenuItem onClick={() => void createUntitledPage()}>
          {t("wiki_collections.menu.create_new_page")}
        </CustomMenu.MenuItem>
        <CustomMenu.MenuItem onClick={() => setIsCreateFolderOpen(true)}>
          {t("wiki_collections.menu.create_new_folder")}
        </CustomMenu.MenuItem>
      </CustomMenu>
      <ProjectCreateFolderModal
        isOpen={isCreateFolderOpen}
        parentId={parentId ?? null}
        pageType={pageType}
        handleClose={() => setIsCreateFolderOpen(false)}
      />
    </>
  );
});
