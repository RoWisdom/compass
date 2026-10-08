/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TAgentTier } from "@/services/agent.service";

/**
 * 档位 → i18n 键。
 *
 * 抽出来的理由很土：这三行原本在 `post-list` / `member-list` / `add-member-modal` 里
 * **各抄了一份**，岗位组会是第四处 —— 同一条规则存两份就是多一份，四份就是四处会
 * 各自漂移的地方。
 *
 * ⚠️ `t()` 取的是 JSON 的**顶层组名**（`ai_members`），不是文件名。这几个键写错了
 * 四道门全绿，只在界面上渲染出键名本身。
 */
export const TIER_LABEL: Record<TAgentTier, string> = {
  readonly: "ai_members.pool.tier_readonly",
  writer: "ai_members.pool.tier_writer",
  ledger: "ai_members.pool.tier_ledger",
};
