/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Wiki 导航树的**纯逻辑**。侧栏（`wiki/sidebar.tsx`）与主列表（`wiki-list-root.tsx`）
 * 共用这里的算法 —— 设计 §3.3 F-4 明确要求「不引入第二套层级算法」。
 *
 * 刻意不做成组件、也不碰任何 store：它只吃「一组 id」+「怎么取父页 id」两个函数，
 * 于是两个调用方各自喂自己的集合（侧栏喂全集、列表喂已过滤集）而算法只有一份。
 */

/**
 * 树里的一行。
 *
 * 刻意是**扁平**的（不是嵌套 children）：侧栏的折叠只需要知道「这一行有没有被折叠的
 * 祖先」，扁平 + 祖先链就够；嵌套结构还要在折叠时做递归过滤，反而更绕。
 */
export type TWikiTreeLine = {
  pageId: string;
  /** 缩进层级，根为 0。父页不在本次集合里的行按**根**渲染（设计 R-2 的兜底）。 */
  depth: number;
  /**
   * 从根到本行**父页**的 id 链（不含本行自己）。
   * 折叠判定只靠它，不需要再往上找。
   */
  ancestorIds: string[];
};

/**
 * 缩进视觉**封顶**在 3 层：再深也不再加宽。
 * 侧栏本来就窄，无限缩进会把标题挤没 —— 设计 §3.3 F-1 明写了封顶。
 *
 * 用固定的 class 表而不是内联 `style`：本仓样式一律走 Tailwind，
 * 内联 padding 会绕开主题变量（且 oxlint 对行内样式有意见）。
 */
export const WIKI_TREE_INDENT_CLASS = ["pl-0", "pl-3", "pl-6", "pl-9"] as const;

/** 这一行该用哪一档缩进。 */
export const wikiTreeIndentClass = (depth: number): string =>
  WIKI_TREE_INDENT_CLASS[Math.min(Math.max(depth, 0), WIKI_TREE_INDENT_CLASS.length - 1)];

/**
 * 下行遍历的深度上限。
 *
 * `parent` 是普通外键，**没有任何约束**禁止 A 的父是 B、B 的父是 A。今天 wiki 的
 * 写入路径造不出环（建页只能挂到**已存在**的页下面，`WikiPageUpdateSerializer`
 * 没有 `parent` 字段），但这是**渲染**路径 —— 数据被绕过 API 改过时不能挂死浏览器。
 * 20 与后端 `_page_ancestors` 的自保上限一致。
 */
const MAX_TREE_DEPTH = 20;

/**
 * 按分区键把页面 id 分组。输入顺序在每个组内**原样保留** ——
 * `treeRows` 的顺序就是渲染顺序（设计 F-1「排序沿用现有顺序」）。
 *
 * 分区键由**服务端**给（设计 B-6），这个函数不推导它。
 */
export const groupPageIdsByPartition = (
  pageIds: string[],
  getPartitionKey: (pageId: string) => string
): Record<string, string[]> => {
  const grouped: Record<string, string[]> = {};
  for (const pageId of pageIds) {
    const key = getPartitionKey(pageId);
    (grouped[key] ??= []).push(pageId);
  }
  return grouped;
};

/**
 * 把**一个分区内**的一组 id 排成一棵（扁平的）树。
 *
 * 两步：① 按父页分组，父页不在本次集合里的当根；② 从根开始深度优先展开，
 * 子行紧跟父行。
 *
 * **父页不在集合里 ⇒ 当根渲染**，这是设计 R-2 的兜底，两种来源共用同一条规则：
 *   - 跨集合/跨分区的孤儿（父页在别的集合、或父页私有而子页公开）——
 *     3.1.1 明确允许子页单独改 `access`，所以**这种孤儿是设计允许的正常结果**；
 *   - 调用方自己过滤掉的（主列表喂进来的是已按搜索过滤的集合，父页可能被滤掉了）。
 * 兜底必须稳：不隐藏、不报错。
 *
 * 排序：根按**输入顺序**，每个父页的子行紧跟它自己。所以调用方喂进来的顺序
 * 决定了顶层的顺序。环或超深的分支会被截断（见 `MAX_TREE_DEPTH`），不会挂死。
 */
export const buildWikiTreeLines = (input: {
  pageIds: string[];
  getParentId: (pageId: string) => string | null;
}): TWikiTreeLine[] => {
  const { pageIds, getParentId } = input;
  const inSet = new Set(pageIds);
  const childIdsByParent = new Map<string, string[]>();
  const rootIds: string[] = [];

  for (const pageId of pageIds) {
    const parentId = getParentId(pageId);
    // `parentId === pageId` 也走根分支：自己当自己的父页属于畸形数据，
    // 当成根渲染至少看得见。
    if (parentId && parentId !== pageId && inSet.has(parentId)) {
      const siblings = childIdsByParent.get(parentId) ?? [];
      siblings.push(pageId);
      childIdsByParent.set(parentId, siblings);
    } else {
      rootIds.push(pageId);
    }
  }

  const lines: TWikiTreeLine[] = [];
  const visited = new Set<string>();

  const walk = (pageId: string, depth: number, ancestorIds: string[]) => {
    if (visited.has(pageId) || depth > MAX_TREE_DEPTH) return;
    visited.add(pageId);
    lines.push({ pageId, depth, ancestorIds });
    for (const childId of childIdsByParent.get(pageId) ?? []) {
      walk(childId, depth + 1, [...ancestorIds, pageId]);
    }
  };

  for (const pageId of rootIds) walk(pageId, 0, []);

  // 成环时（A 的父是 B、B 的父是 A）上面一个根都找不到 —— 那些页面会**整体消失**。
  // 兜底：没被走到的，按输入顺序当根再走一遍（`visited` 保证它仍然终止）。
  for (const pageId of pageIds) {
    if (!visited.has(pageId)) walk(pageId, 0, []);
  }

  return lines;
};

/**
 * 这一行是不是被某个折叠着的祖先藏起来了。
 *
 * 折叠**不改变行本身**，只影响渲染 —— 所以状态与结构是分开的两件事：
 * 折叠集变了不必重建树。
 */
export const isLineHiddenByCollapse = (line: TWikiTreeLine, collapsed: ReadonlySet<string>): boolean =>
  line.ancestorIds.some((ancestorId) => collapsed.has(ancestorId));
