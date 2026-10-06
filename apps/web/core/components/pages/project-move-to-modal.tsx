/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { Box, Folder } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { PageIcon } from "@plane/propel/icons";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EModalWidth, ModalCore } from "@plane/ui";
// components
import { buildWikiTreeLines, wikiTreeIndentClass } from "@/components/pages/tree/page-tree";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useProject } from "@/hooks/store/use-project";
// services
import { PAGE_NODE_TYPE_FOLDER } from "@/services/page";

type Props = {
  isOpen: boolean;
  /** `null` 表示当前没有要移动的节点（弹窗只是被关着）。 */
  pageId: string | null;
  onMoved: () => void;
  handleClose: () => void;
};

/**
 * 项目侧的「移动到」目标选择器（罗盘 Round J）。
 *
 * **不能复用 wiki 的 `move-to-modal.tsx`**：那个按「集合分区」组织目标树
 * （读 `collections` / `treeRows` / `collectionKey`），而项目侧**没有集合这一层**
 * —— 项目的树只有 `parent` 一层关系。硬凑成一个组件会让两边都长出分支。
 *
 * 形状因此更简单：**一棵树 + 一行「顶层」**。
 *
 * - 树：`buildWikiTreeLines` 排出来的全部节点（页面与文件夹都在里面），
 *   与同一个列表里渲染的是**同一套算法**（Round D 起本仓的硬要求）。
 * - 「顶层」：一行，标签是**项目名**、图标 `Box`（与树里的 `Folder` / `PageIcon`
 *   三个层级各一颗、互不复用 —— 照 wiki 的约定）。用项目名而不是「顶层」二字：
 *   零新增 i18n 键（裁定 丙），而项目树的顶层字面上就是这个项目。
 *
 * **不发新请求**：树与索引都在 store 里（`?scope=all` 灌的），打开这个弹窗零网络。
 *
 * **自己 + 自己的全部后代不能当目标**（选它们会成环）。判据走 `buildWikiTreeLines`
 * 给的 `ancestorIds` —— 「祖先链里有我」等价于「我是它的后代」，不用再写一遍遍历
 * （wiki 那个弹窗里手写了一个 while 循环，因为它只有索引、没有行）。
 * 后端那条 400 是兜底；前端不该把明知会失败的选项摆出来。
 */
export const ProjectMoveToModal = observer(function ProjectMoveToModal(props: Props) {
  const { isOpen, pageId, onMoved, handleClose } = props;
  // router
  const { workspaceSlug, projectId } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { pageParentIds, pageNodeTypes, getPageById, moveTo } = usePageStore(EPageStoreType.PROJECT);
  const { getProjectById } = useProject();

  const projectName = getProjectById(projectId?.toString() ?? "")?.name ?? "";

  /** 全部节点（`pageParentIds` 的键就是本项目的每一行 —— `syncTreeRows` 逐行写的）。 */
  const allIds = useMemo(() => Object.keys(pageParentIds), [pageParentIds]);

  const treeLines = useMemo(
    () =>
      buildWikiTreeLines({
        pageIds: allIds,
        getParentId: (id) => pageParentIds[id] ?? null,
      }),
    [allIds, pageParentIds]
  );

  const forbidden = useMemo(() => {
    if (!pageId) return new Set<string>();
    return new Set([
      pageId,
      ...treeLines.filter((line) => line.ancestorIds.includes(pageId)).map((line) => line.pageId),
    ]);
  }, [pageId, treeLines]);

  const handleMove = async (targetParentId: string | null) => {
    if (!workspaceSlug || !projectId || !pageId) return;
    try {
      await moveTo(workspaceSlug, projectId, pageId, { parentId: targetParentId });
    } catch {
      // 复用现成键（「无法移动页面。请重试。」）—— 与 wiki 的同一处同款，不新增文案。
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("toast.error"),
        message: t("wiki_collections.add_existing_page_modal.error_message"),
      });
      return;
    }
    handleClose();
    onMoved();
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} width={EModalWidth.LG}>
      <div className="flex flex-col gap-4 p-5">
        <h3 className="text-16 font-medium">{t("wiki_collections.menu.move_to")}</h3>
        <div className="flex max-h-80 flex-col gap-1 overflow-y-auto">
          {/* 「顶层」= 项目根。项目名当标签，`Box` 当图标。 */}
          <button
            type="button"
            onClick={() => void handleMove(null)}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-13 hover:bg-layer-1"
          >
            <Box className="h-4 w-4 flex-shrink-0 text-tertiary" />
            <span className="truncate">{projectName}</span>
          </button>
          {/* **只把文件夹列为目标**（外加上面那行「顶层」）。
              项目列表里非文件夹父行**没有折叠箭头**（`list/root.tsx` 的 `isFolderRow`
              分支），所以把一个文件夹移到**页面**下面之后，它的整棵子树在列表里永久
              看不见（`isLineHiddenByExpansion` 需要父行进 `expandedSet`，而页面行永远
              进不去），项目侧又没有补偿视图。这与设计「只有文件夹是容器」一致。
              **`forbidden` 仍从完整的 `treeLines` 算**（见上），别跟着这里过滤 ——
              一个节点的祖先链可能穿过某个页面（畸形数据 / R-2 兜底），从过滤后的列表
              算会漏掉那些后代，环就guard 不住了。`depth` 同理仍按完整树算，缩进才对。 */}
          {treeLines
            .filter((line) => pageNodeTypes[line.pageId] === PAGE_NODE_TYPE_FOLDER)
            .map((line) => (
              <button
                key={line.pageId}
                type="button"
                disabled={forbidden.has(line.pageId)}
                onClick={() => void handleMove(line.pageId)}
                className={`flex items-center gap-2 rounded-md py-1.5 pr-2 text-left text-13 hover:bg-layer-1 disabled:cursor-not-allowed disabled:opacity-40 ${wikiTreeIndentClass(line.depth + 1)}`}
              >
                {pageNodeTypes[line.pageId] === PAGE_NODE_TYPE_FOLDER ? (
                  <Folder className="h-4 w-4 flex-shrink-0 text-tertiary" />
                ) : (
                  <PageIcon className="h-4 w-4 flex-shrink-0 text-tertiary" />
                )}
                <span className="truncate">{getPageById(line.pageId)?.name ?? ""}</span>
              </button>
            ))}
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="lg" onClick={handleClose}>
            {t("common.cancel")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
