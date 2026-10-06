/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import type { TPageNavigationTabs } from "@plane/types";
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
 * 项目 Pages 列表的新建入口：**两颗直给的按钮** —— 「添加页面」「添加文件夹」。
 *
 * **没有下拉菜单**（用户裁定 2026-10-06，第三次浏览器验收）。此前这一处是一个
 * `＋` 打开菜单、菜单里再列这两项，菜单里那层点击被用户判为多余的一级
 * —— 「减少层级」。两颗按钮各自直达动作，页面创建少一次点击。
 *
 * 样式与同类页面顶栏一致（`cycles/(list)/header.tsx`、`modules/(list)/header.tsx`、
 * `views/(list)/header.tsx`、`wiki/header.tsx` 都是 `Button variant="primary" size="lg"`
 * 带文字）：建页面是**常用**动作 ⇒ `primary`；建文件夹是**偶发**动作 ⇒ `secondary`。
 * 这个 primary/secondary 配对与周期详情页顶栏（`cycles/(detail)/header.tsx:236`）
 * 同款。**顺带**：不再有 `CustomMenu`，就没有了「按钮套按钮」那个 HTML 非法结构
 * （`CustomMenu` 会给 `customButton` 再包一层 `<button>`）。
 *
 * 文案「添加页面」「添加文件夹」（用户裁定 2026-10-06，第四次浏览器验收）。**不再复用
 * `wiki_collections.menu.*`** —— 那对键是「创建新页面」对「新建文件夹」，措辞本身不齐
 * （上游的漂移），用户这次直接把两颗都点成了「添加…」。
 *
 * 改读 `wiki_collections.actions.*`：`add_page` 是现成的（19 个 locale 都有）；`add_folder`
 * 是**新增**的兄弟键 —— 「添加文件夹」这个说法全库原本一个键都没有。另 17 个语言的译法
 * 不是我随手编的：各自 `add_page` 里的「添加」动词 + 各自 `create_new_folder` 里的
 * 「文件夹」名词，拼起来即得（cs `Přidat složku`、de `Ordner hinzufügen`、ja `フォルダを追加`…）。
 * 代价是一个新增键要写进 19 个语言文件 —— 已付，`check:sync` 19/19 全 100.0%（3858 键）。
 *
 * 同日用户接着要求 **wiki 也用同一对文案**，于是这组键被收敛成**唯一一对**「添加页面 /
 * 添加文件夹」：组名从 `header` 改叫 `actions`（它早就名不副实 —— `add-existing-page-modal`
 * 拿它当**弹窗标题**使），wiki 侧栏 `＋` 菜单的两项、侧栏顶部那颗 `＋ New page`、建页弹窗的
 * 标题与提交按钮、wiki 顶栏按钮、这里的两颗，六处读的都是一对键；被取代的
 * `menu.create_new_page` / `create_new_folder` 已从 19 个语言文件里删除（全仓 0 引用）。
 *
 * 落点由调用方给（`parentId`）—— 在根视图是顶层（不传 / `null`），下钻进某个
 * 文件夹后就是那个文件夹。两种建法都落同一个 `parentId`。
 *
 * 建页面**直接建、直接跳**；建文件夹要先起名字，所以开弹窗 —— 一个空名文件夹会在
 * 用户的**真实 vault** 里造出一个以 uuid 命名的目录（`_file_stem` 在名字净化成空串
 * 时回落到 id），那不能接受。
 *
 * 两颗按钮共用 `disabled={isCreatingPage}`：建页面那一下请求在飞时，两个入口都不该
 * 再被点第二次（原来那颗 `IconButton` 的 `loading` 只做 `disabled || loading`、
 * 不渲转圈，所以这是等价替换）。
 */
export const ProjectCreateActions = observer(function ProjectCreateActions(props: Props) {
  const { pageType, parentId } = props;
  // plane hooks
  const { t } = useTranslation();
  // states
  const [isCreateFolderOpen, setIsCreateFolderOpen] = useState(false);
  // hooks
  const { createUntitledPage, isCreatingPage } = useProjectPageCreate(pageType, parentId);

  return (
    <>
      <Button variant="primary" size="lg" onClick={() => void createUntitledPage()} disabled={isCreatingPage}>
        {t("wiki_collections.actions.add_page")}
      </Button>
      <Button variant="secondary" size="lg" onClick={() => setIsCreateFolderOpen(true)} disabled={isCreatingPage}>
        {t("wiki_collections.actions.add_folder")}
      </Button>
      <ProjectCreateFolderModal
        isOpen={isCreateFolderOpen}
        parentId={parentId ?? null}
        pageType={pageType}
        handleClose={() => setIsCreateFolderOpen(false)}
      />
    </>
  );
});
