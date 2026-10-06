/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { getButtonStyling } from "@plane/propel/button";
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
 * 项目 Pages 列表的新建入口：**一颗按钮、两种节点**。
 *
 * 一颗**带字的实心按钮**（不是图标按钮）—— 与每个同类页面的顶栏一致：周期
 * `cycles/(list)/header.tsx`、模块、视图 `views/(list)/header.tsx`、Wiki 顶栏
 * `wiki/header.tsx` 全是 `Button variant="primary" size="lg"` 带文字的。这一个
 * 位置原来也是（上游那颗 "Add page"），Round J 因为一个入口要装两件事才换成了
 * 光秃秃的 `＋` —— 用户验收时点了出来，改回带字。
 *
 * **样式用 `customButtonClassName={getButtonStyling(...)}` 而不是把 `<Button>` 塞进
 * `customButton`**：`CustomMenu` 拿到 `customButton` 时会**自己再包一层 `<button>`**
 * （`ui/src/dropdowns/custom-menu.tsx:249-262`），塞真按钮进去就是「按钮套按钮」、
 * HTML 非法（本仓有 10 处这么写，都是从上游抄的，本轮只修这一处）。把样式挂在
 * 它自己那颗 `<button>` 上，页面里就只有**一颗**按钮。这也是仓库既有的手法
 * （`rich-filters/add-filters/button.tsx:71`）。
 *
 * 文案复用 `common.add_new`（「添加新的」/ "Add new"，19 个 locale 都有）—— 菜单里
 * 是两件事，所以**不能**沿用 `header.add_page`（那会把「能建文件夹」藏起来）；
 * 视它为 `ariaLabel` 也是同一个串：可见文案与无障碍名字一致。
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
        customButtonClassName={getButtonStyling("primary", "lg")}
        customButton={t("add_new")}
        ariaLabel={t("add_new")}
        // 建页面那一下会立刻跳走，所以不需要 loading 态；但别让它在建的过程中
        // 还能被点第二次（原来那颗 `IconButton` 是这么护的）。
        disabled={isCreatingPage}
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
