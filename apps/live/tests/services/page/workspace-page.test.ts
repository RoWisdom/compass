/**
 * The wiki page service.
 *
 * Two things are load-bearing here and neither is covered anywhere else:
 *
 * 1. `workspace_page` must be dispatched WITHOUT a projectId. Wiki pages are
 *    workspace-scoped — the wiki viewset filters on `is_global=True` and never
 *    on a project, and a page may have no ProjectPage row at all. Falling
 *    through to ProjectPageService would reject the connection in its
 *    constructor (`if (!workspaceSlug || !projectId) throw`).
 *
 * 2. The URLs must NOT contain a `pages/` segment. The wiki routes are
 *    /api/workspaces/<slug>/wiki-pages/<id>/, so a service that inherits the
 *    project default would 404 on every fetch and store.
 *
 * `getPageService` is the only gate: the websocket query params are not
 * validated anywhere (`lib/auth.ts` casts the string with a bare
 * `as TDocumentTypes`), so a wrong branch here is not caught by anything else.
 */
import { describe, expect, it, vi } from "vitest";
import { getPageService } from "@/services/page/handler";
import { ProjectPageService } from "@/services/page/project-page.service";
import { WorkspacePageService } from "@/services/page/workspace-page.service";
import type { HocusPocusServerContext } from "@/types";

const BASE = "/api/workspaces/926/wiki-pages";

const makeContext = (over: Partial<HocusPocusServerContext> = {}): HocusPocusServerContext => ({
  workspaceSlug: "926",
  projectId: null,
  cookie: "session=abc",
  documentType: "workspace_page",
  userId: "u1",
  ...over,
});

describe("getPageService dispatch", () => {
  it("dispatches workspace_page with no projectId", () => {
    const service = getPageService("workspace_page", makeContext());
    expect(service).toBeInstanceOf(WorkspacePageService);
  });

  it("still dispatches project_page", () => {
    const service = getPageService("project_page", makeContext({ documentType: "project_page", projectId: "proj-1" }));
    expect(service).toBeInstanceOf(ProjectPageService);
  });
});

describe("WorkspacePageService — wiki URLs drop the `pages/` segment", () => {
  const makeService = () => new WorkspacePageService({ workspaceSlug: "926", cookie: "session=abc" });

  it("fetchDetails GETs <base>/<id>/", async () => {
    const service = makeService();
    const get = vi.spyOn(service, "get").mockResolvedValue({ data: {} } as never);

    await service.fetchDetails("p1");

    expect(get).toHaveBeenCalledWith(`${BASE}/p1/`, expect.anything());
    expect(get.mock.calls[0][0]).not.toContain("/pages/");
  });

  it("fetchDescriptionBinary GETs <base>/<id>/description/", async () => {
    const service = makeService();
    const get = vi.spyOn(service, "get").mockResolvedValue({ data: Buffer.from([1]) } as never);

    await service.fetchDescriptionBinary("p1");

    expect(get).toHaveBeenCalledWith(`${BASE}/p1/description/`, expect.anything());
    expect(get.mock.calls[0][0]).not.toContain("/pages/");
  });

  it("updateDescriptionBinary PATCHes <base>/<id>/description/", async () => {
    const service = makeService();
    const patch = vi.spyOn(service, "patch").mockResolvedValue({ data: {} } as never);

    await service.updateDescriptionBinary("p1", {
      description_binary: "AA==",
      description_html: "<p>x</p>",
      description_json: {},
    });

    expect(patch).toHaveBeenCalledWith(`${BASE}/p1/description/`, expect.anything(), expect.anything());
    expect(patch.mock.calls[0][0]).not.toContain("/pages/");
  });

  it("updatePageProperties PATCHes <base>/<id>/", async () => {
    const service = makeService();
    const patch = vi.spyOn(service, "patch").mockResolvedValue({ data: {} } as never);

    await service.updatePageProperties("p1", { data: { name: "新标题" } });

    expect(patch).toHaveBeenCalledWith(`${BASE}/p1/`, { name: "新标题" }, expect.anything());
    expect(patch.mock.calls[0][0]).not.toContain("/pages/");
  });
});

describe("WorkspacePageService — guard clauses", () => {
  it("rejects a missing workspaceSlug", () => {
    expect(() => new WorkspacePageService({ workspaceSlug: null, cookie: "session=abc" })).toThrow();
  });

  it("rejects a missing cookie", () => {
    expect(() => new WorkspacePageService({ workspaceSlug: "926", cookie: null })).toThrow();
  });
});
