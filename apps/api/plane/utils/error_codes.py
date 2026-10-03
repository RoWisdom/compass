# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

ERROR_CODES = {
    # issues
    "INVALID_ARCHIVE_STATE_GROUP": 4091,
    "INVALID_ISSUE_DATES": 4100,
    "INVALID_ISSUE_START_DATE": 4101,
    "INVALID_ISSUE_TARGET_DATE": 4102,
    # pages
    "PAGE_LOCKED": 4701,
    "PAGE_ARCHIVED": 4702,
    # 文件夹没有正文（Confluence F2）。正文端点对 `node_type="folder"` 的行用它回 400。
    # 跟 4701/4702 同一个 47xx 段（页面类错误），下一个空位就是 4703。
    "PAGE_IS_FOLDER": 4703,
}
