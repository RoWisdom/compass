/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
// components
import { AddExistingPageModal } from "@/components/pages/wiki/add-existing-page-modal";
// services
import { canIncludeIntoCollection } from "@/services/page";

type TWikiIncludeModalContext = {
  isOpen: boolean;
  open: () => void;
};

const WikiIncludeModalContext = createContext<TWikiIncludeModalContext | undefined>(undefined);

/**
 * 「收录（Add page）」弹窗的**唯一**所有者，挂在 `wiki/layout.tsx` 上 ——
 * 它同时包住顶栏（`WikiHeader`）与列表（`Outlet`），两处都要能开同一个弹窗。
 *
 * 为什么必须提到 layout 这一层：`AddExistingPageModal` 原来只挂在列表的**空态分支**里
 * （`wiki-list-main-content.tsx` 的 `includeModal`），而分区一旦有页面，空态分支就再也进不去
 * —— 收录入口永久消失。`general` 又是唯一可达目标（集合 CRUD 属 Phase 2），
 * 于是整个工作区只能收录一次。顶栏按钮是常驻的，所以弹窗不能再属于列表。
 *
 * 只有**一份** state、**一份**弹窗实例：顶栏按钮与空态 CTA 都调这里的 `open()`。
 * 挂两份弹窗或两份 state 会出现「点了没反应」（那是本分支 fix #4 刚消除过的那类重复）。
 */
export function WikiIncludeModalProvider(props: { children: React.ReactNode }) {
  const { children } = props;
  // states
  const [isModalOpen, setIsModalOpen] = useState(false);
  // router
  const searchParams = useSearchParams();
  // 与 `wiki/header.tsx`、`wiki/page.tsx` 同一写法
  const collection = searchParams.get("collection") ?? "general";
  // 只有 `general` 与自建集合能接收收录（`private`/`shared`/`archived` 是派生分区，
  // 理由见 `canIncludeIntoCollection` 的注释）。
  // **门控放在 provider 里**，而不是只放在按钮上：换分区时 provider 不重建、`isModalOpen`
  // 会留着 —— 只门控按钮的话，在 general 打开弹窗再切到 private，弹窗会挂在那儿继续可选页。
  const canIncludeHere = canIncludeIntoCollection(collection);

  // 同时把 state 清掉：只算 `canIncludeHere && isModalOpen` 的话，在上面那个场景里回到
  // general 会凭空弹出来（它只是刚才被门控藏起来了，并没有被关过）。
  useEffect(() => {
    if (!canIncludeHere) setIsModalOpen(false);
  }, [canIncludeHere]);

  const isOpen = canIncludeHere && isModalOpen;

  const value = useMemo<TWikiIncludeModalContext>(() => ({ isOpen, open: () => setIsModalOpen(true) }), [isOpen]);

  return (
    <WikiIncludeModalContext.Provider value={value}>
      {children}
      <AddExistingPageModal isOpen={isOpen} collection={collection} handleClose={() => setIsModalOpen(false)} />
    </WikiIncludeModalContext.Provider>
  );
}

/**
 * provider 外调用要**抛错**，不要静默返回 `undefined` —— 那会变成「点了没反应」，
 * 是本次要修的那类 bug 的同一个形状（入口在，动作不生效）。
 */
export const useWikiIncludeModal = (): TWikiIncludeModalContext => {
  const context = useContext(WikiIncludeModalContext);
  if (!context) throw new Error("useWikiIncludeModal must be used within a WikiIncludeModalProvider");
  return context;
};
