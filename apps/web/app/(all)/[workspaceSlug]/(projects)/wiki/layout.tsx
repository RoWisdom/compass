/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// components
import { Outlet } from "react-router";
import { AppHeader } from "@/components/core/app-header";
import { ContentWrapper } from "@/components/core/content-wrapper";
import { WikiIncludeModalProvider } from "@/components/pages/wiki/wiki-include-modal-context";
// local imports
import { WikiHeader } from "./header";

export default function WikiLayout() {
  // provider 必须包住 `AppHeader`（`header` 是 ReactNode，在 AppHeader 内部渲染，
  // 所以顶栏的按钮拿得到 context）与 `Outlet`（列表与详情页都要能开收录弹窗）。
  return (
    <WikiIncludeModalProvider>
      <AppHeader header={<WikiHeader />} />
      <ContentWrapper>
        <Outlet />
      </ContentWrapper>
    </WikiIncludeModalProvider>
  );
}
