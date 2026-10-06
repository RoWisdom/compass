/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
// types
import type { TPageNavigationTabs } from "@plane/types";
// helpers
import { cn } from "@plane/utils";

type TPageTabNavigation = {
  workspaceSlug: string;
  projectId: string;
  pageType: TPageNavigationTabs;
};

// pages tab options
const pageTabs: { key: TPageNavigationTabs; label: string }[] = [
  {
    key: "public",
    label: "Public",
  },
  {
    key: "private",
    label: "Private",
  },
  {
    key: "archived",
    label: "Archived",
  },
];

export function PageTabNavigation(props: TPageTabNavigation) {
  const { workspaceSlug, projectId, pageType } = props;
  const searchParams = useSearchParams();

  const handleTabClick = (e: React.MouseEvent<HTMLAnchorElement>, tabKey: TPageNavigationTabs) => {
    if (tabKey === pageType) e.preventDefault();
  };

  /**
   * 保留当前 URL 的其余 query（尤其 `?folder=`）—— tab 是一个**作用域**，
   * 与同一个 header 里的搜索 / 筛选 / 排序（都活在 store 里、下钻后自然保留）同级。
   * 硬编码 `?type=` 会在下钻中切 tab 时把 `folder` 摘掉，静默把人踢回根视图。
   */
  const buildTabHref = (tabKey: TPageNavigationTabs) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("type", tabKey);
    return `/${workspaceSlug}/projects/${projectId}/pages?${params.toString()}`;
  };

  return (
    <div className="relative flex h-full items-center">
      {pageTabs.map((tab) => (
        <Link
          key={tab.key}
          href={buildTabHref(tab.key)}
          onClick={(e) => handleTabClick(e, tab.key)}
          className="flex h-full flex-col"
        >
          <div
            className={cn(`flex flex-1 items-center justify-center px-4 text-13 font-medium transition-all`, {
              "text-accent-primary": tab.key === pageType,
            })}
          >
            {tab.label}
          </div>
          <div
            className={cn(`w-full rounded-t border-t-2 border-transparent transition-all`, {
              "border-accent-strong": tab.key === pageType,
            })}
          />
        </Link>
      ))}
    </div>
  );
}
