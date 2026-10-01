/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useRef } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { PageIcon } from "@plane/propel/icons";
import { ContentWrapper, Loader } from "@plane/ui";
import { calculateTimeAgoShort, getPageName } from "@plane/utils";
// components
import { ListItem } from "@/components/core/list";
// hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
// services
import type { TPredefinedCollectionKey } from "@/services/page";
// types
import type { TWorkspacePage } from "@/store/pages/workspace-page";

/** 「最近编辑」最多列几行。226 实测 wiki 收录页才 4 条（2026-10-01），不需要分页。 */
const RECENT_PAGES_LIMIT = 8;

/** 首页的版式常量，两节共用一份，免得两处各写一遍再漂。 */
const SECTION_TITLE_CLASS = "text-14 font-semibold text-tertiary";

/**
 * 预置分区里**唯一**进「集合概览」的那个。理由见下方 `collectionRows` 的注释。
 *
 * 类型写成 `TPredefinedCollectionKey` 而不是 `string`：下面要把它喂进
 * `t(\`wiki_collections.predefined.${key}\`)`，那个模板串的字面量类型必须收窄到键的联合，
 * 宽成 `string` 就过不了 `t()` 的签名。
 */
const GENERAL_COLLECTION_KEY: TPredefinedCollectionKey = "general";

type Props = {
  /**
   * 由 `wiki/page.tsx` 传入，**不在这里 `useParams()`** —— 本组件挂在该路由下，
   * `useParams()` 拿得到，但 `WikiListView` 已有传 prop 的先例，跟随即可。
   */
  workspaceSlug: string;
};

/**
 * Wiki 首页（裸 URL `/{slug}/wiki/`，即 `?collection` 缺省时）。
 *
 * **纯视图：不发请求、不写 store。** 两处数据都已在 store 里 —— 侧栏随
 * `wiki/layout.tsx` 常驻，它已经调过 `fetchWikiTree`（填 `data` 与 `treeRows`）
 * 与 `fetchCollections`（填 `predefined` 与 `collections`）。首页只是换个读法。
 * 这也是**不在这里再挂一对 `useSWR`** 的原因：SWR 键（`WIKI_TREE_${slug}` /
 * `WIKI_COLLECTIONS_${slug}`）是侧栏里的内联模板串，抄一份就有了第二个真相源 ——
 * 侧栏哪天改了键，这里不会报错，只会**静默多发一个请求**。
 */
export const WikiHome = observer(function WikiHome(props: Props) {
  const { workspaceSlug } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { treeRows, getPageById, predefined, collections } = usePageStore(EPageStoreType.WORKSPACE);
  // refs
  // `ListItem` 的 `parentRef` 是给行内浮层定位用的。`recents/page.tsx:46` 也是**整个列表共用
  // 一个 ref**，这里跟随（不是每行一个）。
  const parentRef = useRef<HTMLDivElement>(null);

  // 集合列表还没回来。判据取 `predefined.length === 0` 是**可靠**的而不是碰巧：
  // 后端那条路径是无条件遍历 `PREDEFINED_KEYS` 产出的
  // （`apps/api/plane/app/views/page/collection.py:102`），
  // 所以「4 个预置分区全空」只在**请求未落地/失败**时出现，不会因为「本来就没页面」出现。
  // （用 `treeRows.length === 0` 判就不行 —— 那个与「一页都没有」不可区分。）
  const isCollectionsLoading = predefined.length === 0;

  const recentPages = treeRows.map((row) => getPageById(row.pageId)).filter((page): page is TWorkspacePage => !!page);

  // 按 `updated_at` 倒序（最新在前）。
  //
  // 就地 `sort` 在这里是**安全**的，也是唯一可行的一条：`recentPages` 是上面
  // `.filter()` 刚生成的新数组，动它不会碰到 `treeRows` / `store.data`。
  // oxlint 建议的 `toSorted` **不能用** —— 不在本 app 的 tsconfig lib 里，
  // `check:types` 实测报 TS2550（要求 es2023）。store 自己那处 `updated_at` 排序
  // （`workspace-page.store.ts:159-165`）也是 `sort` + 同一行抑制注释。
  //
  // 抑制注释必须**紧挨 `.sort(` 那一行**：写成 `const x = [...].sort(...)` 的链式形状时，
  // 注释上一行、`.sort(` 下一行，抑制会失效（我第一版就是这么写的，白多一条 warning）。
  // oxlint-disable-next-line unicorn/no-array-sort
  recentPages.sort((a, b) => new Date(b.updated_at ?? 0).getTime() - new Date(a.updated_at ?? 0).getTime());

  const topRecentPages = recentPages.slice(0, RECENT_PAGES_LIMIT);

  // 预置分区与自建集合**同一张表**：它们在 `?collection=` 里本来就是同一个值域
  // （预置键或 uuid），侧栏的 `goTo` 也是这么拼的。
  //
  // **只镜像侧栏「集合」组里的东西：常规 + 自建集合**（用户 2026-10-01 裁定）。
  // 另外三个预置分区**一律不进概览**，理由是三条各自独立、都成立的：
  //
  // - `private` —— 用户 2026-10-01 把它从侧栏摘掉了（「现阶段用不上」，
  //   `sidebar.tsx` 的 `PARTITION_ROWS` 注释），首页不该把那条入口放回来；
  // - `archived` —— 同一轮被降成**不可点的分组标题**，代价记账里写明
  //   「归档分区的列表页从此没有导航入口」，首页列上就等于撤销那条记账；
  // - `shared` —— `resolve_collection_key` **永不返回**它（开源版没有发布字段，恒空），
  //   本来也不会出现，写在这里只是把「为什么不用过滤」记明白。
  //
  // 两条不同口径，别合并：
  // - **常规恒显示**（哪怕 0 页）—— 它是默认集合，侧栏那颗也是恒显示的；
  //   0 页时从概览里消失，「集合概览」就看不到默认集合了（226 现状正是 0 页）。
  // - **自建集合只列非空的**（用户 2026-10-01 裁定）—— 空的（如 226 的「测试222」）不占位。
  const collectionRows = [
    {
      key: GENERAL_COLLECTION_KEY,
      label: t(`wiki_collections.predefined.${GENERAL_COLLECTION_KEY}`),
      count: predefined.find((item) => item.key === GENERAL_COLLECTION_KEY)?.page_count ?? 0,
    },
    ...collections
      .filter((collection) => collection.page_count > 0)
      .map((collection) => ({ key: collection.id, label: collection.name, count: collection.page_count })),
  ];

  if (isCollectionsLoading)
    return (
      <ContentWrapper className="mx-auto scrollbar-hide gap-6 bg-surface-1 px-page-x">
        <div className="mx-auto flex w-full max-w-[800px] flex-col gap-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <Loader key={i} className="relative flex items-center gap-2 p-3">
              <Loader.Item width="220px" height="20px" />
            </Loader>
          ))}
        </div>
      </ContentWrapper>
    );

  // **没有「整页空态」这个分支**，是刻意的：`collectionRows` 里那颗「常规」是恒在的，
  // 所以本组件永远至少有一行可渲染。空 Wiki 落在「最近编辑」那一节的空态上，
  // 而「常规 0」那一行本身就把用户带去列表页 —— 那边有带收录 CTA 的空态
  // （`wiki-list-main-content.tsx`），比在首页再复述一遍更省一层，也别留个够不到的分支。
  return (
    <ContentWrapper className="mx-auto scrollbar-hide gap-6 bg-surface-1 px-page-x">
      <div className="mx-auto flex w-full max-w-[800px] flex-col gap-8">
        <div className="flex flex-col">
          <div className={`mb-2 ${SECTION_TITLE_CLASS}`}>{t("wiki_home.recent_title")}</div>
          {topRecentPages.length > 0 ? (
            topRecentPages.map((page) => (
              <ListItem
                key={page.id}
                itemLink={`/${workspaceSlug}/wiki/${page.id}`}
                title={getPageName(page.name)}
                prependTitleElement={
                  <div className="grid size-8 flex-shrink-0 place-items-center rounded-sm bg-layer-2">
                    <PageIcon className="size-4 text-tertiary" />
                  </div>
                }
                // 时间用 **`calculateTimeAgoShort`（`3m`/`2h`/`5d`），不用 `calculateTimeAgo`** ——
                // 后者调 date-fns 时**不传 locale**（`packages/utils/src/datetime.ts:178`），
                // 中文界面里会印出 `3 minutes ago`。这是**有意与 Projects 首页的 recents
                // 不同**（`recents/page.tsx:63` 用的就是它），别为了「对齐」换回去。
                appendTitleElement={
                  <div className="flex-shrink-0 text-11 font-medium text-placeholder">
                    {calculateTimeAgoShort(page.updated_at ?? null)}
                  </div>
                }
                parentRef={parentRef}
                className="my-auto border-none !px-2 py-3"
                itemClassName="my-auto bg-layer-transparent"
              />
            ))
          ) : (
            <div className="rounded-lg bg-layer-1 px-3 py-6 text-center text-13 text-tertiary">
              {t("wiki_collections.list.no_pages_title")}
            </div>
          )}
        </div>

        {/* 不加 `length > 0` 守卫 —— 上面「常规」那颗恒在，永远非空（见 `collectionRows`）。
            节标题复用 `fallback_name`（「集合」）：这一节就是侧栏那个「集合」组，
            不为这四个字再添一个键（添键要在 19 个语言文件里都添）。 */}
        <div className="flex flex-col">
          <div className={`mb-2 ${SECTION_TITLE_CLASS}`}>{t("wiki_collections.fallback_name")}</div>
          {collectionRows.map((row) => (
            <ListItem
              key={row.key}
              itemLink={`/${workspaceSlug}/wiki/?collection=${row.key}`}
              title={row.label}
              appendTitleElement={<div className="flex-shrink-0 text-11 font-medium text-placeholder">{row.count}</div>}
              parentRef={parentRef}
              className="my-auto border-none !px-2 py-3"
              itemClassName="my-auto bg-layer-transparent"
            />
          ))}
        </div>
      </div>
    </ContentWrapper>
  );
});
