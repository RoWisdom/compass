/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { Box, Folder } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { ChevronRightIcon } from "@plane/propel/icons";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EModalWidth, ModalCore } from "@plane/ui";
import { cn } from "@plane/utils";
// components
import { buildWikiTreeLines, wikiTreeIndentClass } from "@/components/pages/tree/page-tree";
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
 *   · 树里任意**文件夹行** ⇒ 挂到它下面。
 *
 * **只把文件夹列为目标**（用户裁定 2026-10-06）。口径与 `project-move-to-modal.tsx`
 * 逐字一致 —— 那边早就写死了「只有文件夹是容器」，**本文件是当时的例外**。
 * （原文写的是「树里任意能容纳子项的行（文件夹或页面）」—— 页面这一档已摘掉。）
 *
 * 依据是用户对侧栏 `＋` 的同一条读法：**页面这一行的身份是「文档」，只有文件夹是「容器」**。
 * 侧栏页面行摘掉 `＋` 之后（`53d6d6412`），「把 A 页挂到 B 页下」在这里成了**最后一处**
 * 入口 —— 用户看过那句「唯一剩下的建子页面路径」之后，把它也摘了。
 *
 * ⇒ **UI 上再没有任何路径能让一个页面成为父行**。已有关系不受影响：子页照旧在侧栏与列表里
 * 嵌套显示，也仍能**移出去**（移到文件夹或集合顶层）；丢的只是「再建一条」。后端
 * `create_page` 仍收任意已收录的页面当 `parent`（本轮不动后端），所以 API 层面这条路还在。
 *
 * **过滤放在渲染处，不是喂给 `buildWikiTreeLines` 之前**：`depth` 仍按**整棵树**算，缩进才对
 * 得上；`forbidden` 更是必须按完整索引算 —— 祖先链可能穿过一个页面（畸形数据 / R-2 兜底），
 * 从过滤后的列表算会漏掉那些后代，环就 guard 不住了。同款理由见
 * `project-move-to-modal.tsx:118-125`。
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
 *
 * **集合行带折叠箭头、默认收起**（Round G）—— 一打开只看到集合名，点箭头才铺出该集合的
 * 整棵子树。被移动节点**所在**的那个集合会自动展开，其余保持收起（否则收起之后用户
 * 找不到自己那一页）。状态存**展开**集、空集即全收起，与侧栏 `expandedPartitionKeys`
 * 同一口径。**只折集合层**，树内部不再每层可折 —— 知情的取舍，见设计 §5。
 *
 * 图标照侧栏集合行那段约定：集合 `Box` / 文件夹 `Folder` —— 三个层级各一颗、互不复用
 * （第三档 `PageIcon` 在本弹窗里**看不到了**：页面不再是目标）。
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

  /**
   * 哪些集合是**展开**的。存展开集、空集 ⇒ 全收起 —— 与侧栏 `expandedPartitionKeys`
   * 同一口径。理由见 `wiki-tree.ts:150-163`：存折叠集的话默认值得是
   * 「全部集合键」，而集合是异步来的、之后还会新增，那个默认值每加一个就要补一次，
   * 漏一处就成了「新集合默认展开」，与「默认收起」自相矛盾（设计 G-2 / G-8）。
   */
  const [expandedGroupKeys, setExpandedGroupKeys] = useState<string[]>([]);

  /**
   * 被移动的节点落在哪个集合 —— 弹窗一打开就把它展开（设计 G-10）。不这么做的话，
   * 「默认收起」会让用户**找不到自己那一页在哪儿**。同源：侧栏那段自动展开锚点祖先链的
   * effect（锚点是 `activePageId ?? explicitFolder`）。
   *
   * **先压成字符串再进依赖**：`treeRows` 是 store 上的数组，引用一变 effect 就重跑，
   * 而 effect 里又 setState —— 把数组本身放进依赖是**死循环**（设计 §8 那条风险）。
   */
  const anchorGroupKey = useMemo(
    () => (pageId ? (treeRows.find((row) => row.pageId === pageId)?.collectionKey ?? null) : null),
    [pageId, treeRows]
  );

  /**
   * **只在「打开」与「锚点真的换了」时跑**。用户手动开合改的是 `expandedGroupKeys`，
   * 它**不在**依赖里 —— 所以手动状态不会被程序掰回去（侧栏同一个 effect 同款
   * 「**不强行掰回来**」：那颗箭头不该变成一个点了没反应的死控件）。设计 G-6。
   */
  useEffect(() => {
    if (!isOpen) return;
    setExpandedGroupKeys(anchorGroupKey ? [anchorGroupKey] : []);
  }, [isOpen, anchorGroupKey]);

  const toggleGroup = (key: string) =>
    setExpandedGroupKeys((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key]
    );

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
          {groups.map((group) => {
            const isExpanded = expandedGroupKeys.includes(group.key);
            // **只把文件夹列出来**（见 docblock）。过滤在这里做、不在 `linesFor` 里做 ——
            // `depth` 与 `forbidden` 都要按**整棵树**算，理由同 `project-move-to-modal`。
            // 箭头也跟着这份过滤后的长度走：一个只装页面的集合铺开来空空如也，
            // 那就该与空集合一样**不给箭头**（G-9 的等宽占位）。
            const targetLines = group.lines.filter((line) => pageNodeTypes[line.pageId] === PAGE_NODE_TYPE_FOLDER);
            return (
              <div key={group.key} className="flex flex-col">
                {/* **两个热区**（设计 G-4）：标签 = 移到该集合顶层（行为与改动前**逐字不变**），
                    箭头 = 展开/收起。与侧栏的分组标题**刻意不同** —— 那边点标题也折叠
                    （`renderGroupHeader` 的标题按钮），因为这边的整行是一个**动作**。 */}
                <div className="flex items-center gap-2 rounded-md px-2 py-1.5 text-13 hover:bg-layer-1">
                  <button
                    type="button"
                    disabled={forbidden.has(group.key)}
                    onClick={() =>
                      handleMove({ collectionId: group.key === "general" ? null : group.key, parentId: null })
                    }
                    className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {/* 集合那一行是 `Box`（lucide），**不是**下面树行那颗 `Folder` ——
                        侧栏写死的约定：集合 `Box` / 文件夹 `Folder` / 页面 `PageIcon`，
                        三个层级各一颗、互不复用（设计 G-1；页面那一档本弹窗已不用，见 docblock）。 */}
                    <Box className="h-4 w-4 flex-shrink-0 text-tertiary" />
                    <span className="truncate">{group.label}</span>
                  </button>
                  {/* 箭头**常显**，不做「悬停才浮出」（设计 G-3）—— 这是侧栏对
                      「分组标题」那一档的口径：收起后它是横躺的、
                      一眼能看见也能点开。没有子行就不给箭头（G-9），留**等宽占位**保对齐
                      —— 按钮现在是 `p-1` + `size-3` 图标 = 20px，占位用 `size-5`。 */}
                  {targetLines.length > 0 ? (
                    <button
                      type="button"
                      onClick={() => toggleGroup(group.key)}
                      aria-expanded={isExpanded}
                      aria-label={group.label}
                      className="flex-shrink-0 p-1 text-tertiary"
                    >
                      <ChevronRightIcon className={cn("size-3 transition-transform", { "rotate-90": isExpanded })} />
                    </button>
                  ) : (
                    <span className="size-5 flex-shrink-0" />
                  )}
                </div>
                {isExpanded &&
                  targetLines.map((line) => (
                    <button
                      key={line.pageId}
                      type="button"
                      disabled={forbidden.has(line.pageId)}
                      onClick={() => handleMove({ collectionId: null, parentId: line.pageId })}
                      className={`flex items-center gap-2 rounded-md py-1.5 pr-2 text-left text-13 hover:bg-layer-1 disabled:cursor-not-allowed disabled:opacity-40 ${wikiTreeIndentClass(line.depth + 1)}`}
                    >
                      {/* 这里**只有文件夹**（`targetLines` 已滤过），所以不再写那个
                          页面/文件夹二选一的三元 —— 它是渲染层的死分支。 */}
                      <Folder className="h-4 w-4 flex-shrink-0 text-tertiary" />
                      <span className="truncate">{getPageById(line.pageId)?.name ?? ""}</span>
                    </button>
                  ))}
              </div>
            );
          })}
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
