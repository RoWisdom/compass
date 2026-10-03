/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { Folder } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { PageIcon } from "@plane/propel/icons";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EModalWidth, ModalCore } from "@plane/ui";
// components
import { buildWikiTreeLines, wikiTreeIndentClass } from "@/components/pages/wiki/wiki-tree";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
// services
import { PAGE_NODE_TYPE_FOLDER } from "@/services/page";

type Props = {
  isOpen: boolean;
  /** `null` 表示当前没有要移动的节点（弹窗只是被关着）。 */
  pageId: string | null;
  /**
   * 动作落库后重拉**当前视图**。**由调用方给**，不由本弹窗自己打端点。
   *
   * 理由与 Round D 那条一样：这个弹窗会从**文件夹视图**里被打开，那时「当前键」是一个
   * 文件夹 uuid —— 打 `?collection=<uuid>` 后端按 `collection_id` 过滤后会**静默返回
   * 空列表**，把用户右半边无声清空。只有调用方知道自己在哪种视图里。
   */
  onMoved: () => void;
  handleClose: () => void;
};

/**
 * 「移动到」的目标选择器 —— **一棵位置树**，不是一个集合列表。
 *
 * 语义照 Confluence Cloud 的 Move：目标是「位置」。所以：
 *   · 一个**集合**（含「常规」）那一行 ⇒ 移到该集合顶层；
 *   · 树里任意**能容纳子项的行**（文件夹或页面）⇒ 挂到它下面。
 *
 * **为什么页面也能当目标**：本仓的 wiki 从 Round C 起支持子页面，`create_page` 也一直
 * 收任意已收录的页面当 `parent`。只列文件夹会凭空造出一条「建得出来、搬不进去」的不对称。
 *
 * **为什么只列这些集合**：`general` + 用户自建集合是有合法 `collection_id` 的两种。
 * `private`/`shared`/`archived` 是**派生**分区：一行落在其中哪个由 `access`/`archived_at`
 * 决定（`resolve_collection_key` 的优先级 archived > private > collection_id > general），
 * 与它自己的 `collection_id` 无关 —— 所以 `collection_id` 指不过去，把它们当目标只会把
 * `collection_id` 变成 `null`、页面落回 general，而 UI 在骗用户。
 *
 * **派生分区里的行同样不列**（是不列，不是列出来再置灰）：树按分区键分组建（下面
 * `linesFor`；侧栏同款 —— 见 `linesByPartition` 那段注释「父页在别的分区时，子行在本
 * 分区里当根渲染」，设计 R-2 的兜底），跨分区的父链接两边都认不出来；而移动**不搬
 * `access`**（移动块只写 `parent_id` 与由目标推导的 `collection_id`），所以一个**本来
 * 不在**该派生分区里的页面挂上去之后，仍留在自己分区里当**顶层孤儿** —— 用户点了「挂到
 * 这一行下面」，嵌套却没发生（只可能连带换了集合），正是上面那句「UI 在骗用户」。
 * 「常规」不受影响：它的行与被移动页面同属 general，父链接落在组内。
 *
 * **边界**（本轮不处理）：若被移动页面**本身就在**同一个派生分区里，那条父链接是分区
 * 内的，嵌套会正常发生 —— 但设计 §5.1 的目标列表只给 `general` + 用户自建集合，没有
 * 这种入口。这是设计取舍，不是这里的实现缺陷。
 *
 * **不发新请求**：树与索引都在 store 里（`fetchWikiTree` 灌的），侧栏随 layout 常驻 ——
 * 打开这个弹窗是零网络。
 *
 * 排序与缩进复用 `buildWikiTreeLines`（Round D：不引入第二套层级算法）。
 */
export const MoveToModal = observer(function MoveToModal(props: Props) {
  const { isOpen, pageId, onMoved, handleClose } = props;
  // router
  const { workspaceSlug } = useParams();
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { collections, treeRows, pageParentIds, pageNodeTypes, getPageById, moveTo } = usePageStore(
    EPageStoreType.WORKSPACE
  );

  /**
   * 自己 + 自己的全部后代 —— 这些行**不能**当目标，选它们会成环。
   * 后端那条 400 是兜底；前端不该把明知会失败的选项摆出来。
   */
  const forbidden = useMemo(() => {
    const blocked = new Set<string>();
    if (!pageId) return blocked;
    blocked.add(pageId);
    // 反复扫到不再增长为止。`pageParentIds` 是**索引**、不是邻接表，所以没有
    // 「从上往下走」的现成顺序可用；树是人的规模，这样写最省心也最不会挂死。
    let grew = true;
    while (grew) {
      grew = false;
      for (const [childId, parentId] of Object.entries(pageParentIds)) {
        if (parentId && blocked.has(parentId) && !blocked.has(childId)) {
          blocked.add(childId);
          grew = true;
        }
      }
    }
    return blocked;
  }, [pageId, pageParentIds]);

  /** 每个集合一组：集合那一行（可选中）+ 它下面的树。 */
  const groups = useMemo(() => {
    const linesFor = (collectionKey: string) =>
      buildWikiTreeLines({
        pageIds: treeRows.filter((row) => row.collectionKey === collectionKey).map((row) => row.pageId),
        getParentId: (id: string) => pageParentIds[id] ?? null,
      });

    return [
      // `general` 是唯一合法的预置目标（对应 `collection_id = null`），排在最前。
      { key: "general", label: t("wiki_collections.predefined.general"), lines: linesFor("general") },
      ...collections.map((item) => ({ key: item.id, label: item.name, lines: linesFor(item.id) })),
    ];
  }, [collections, treeRows, pageParentIds, t]);

  const handleMove = async (target: { collectionId: string | null; parentId: string | null }) => {
    if (!workspaceSlug || !pageId) return;
    try {
      await moveTo(workspaceSlug, pageId, target);
    } catch {
      // 失败提示复用现成键（「无法移动页面。请重试。」）—— 语义正确，不新增文案。
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.toast.error"),
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
          {groups.map((group) => (
            <div key={group.key} className="flex flex-col">
              <button
                type="button"
                disabled={forbidden.has(group.key)}
                onClick={() => handleMove({ collectionId: group.key === "general" ? null : group.key, parentId: null })}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-13 hover:bg-layer-1 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <PageIcon className="h-4 w-4 text-tertiary" />
                <span className="truncate">{group.label}</span>
              </button>
              {group.lines.map((line) => (
                <button
                  key={line.pageId}
                  type="button"
                  disabled={forbidden.has(line.pageId)}
                  onClick={() => handleMove({ collectionId: null, parentId: line.pageId })}
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
