/**
 * The `pageUrl` seam must not move the project-page URLs.
 *
 * `PageCoreService` is shared: `ProjectPageService` (project pages) and
 * `WorkspacePageService` (wiki pages) both inherit it. The project routes are
 * `/api/workspaces/<slug>/projects/<pid>/pages/<id>/`; the wiki routes have no
 * `pages/` segment. The seam is what lets the wiki subclass drop that one
 * segment instead of copying five method bodies.
 *
 * That refactor is only safe if the default stays byte-identical to what every
 * existing caller already produced — a silent `pages/` change here would take
 * the collaborative editor offline for every project page, and nothing else in
 * this app covers that URL shape. This file is that test.
 *
 * NOTE ON SHAPE: this is a *characterization* test, not the red half of a
 * red-green cycle. It pins behaviour that is already correct, so it is green
 * on the first run — that is the point. It must stay green across the
 * refactor; a red run afterwards means the refactor changed the URLs.
 */
import { describe, expect, it, vi } from "vitest";
import { ProjectPageService } from "@/services/page/project-page.service";

const BASE = "/api/workspaces/926/projects/proj-1";

const makeService = () => new ProjectPageService({ workspaceSlug: "926", projectId: "proj-1", cookie: "session=abc" });

describe("ProjectPageService — page URLs keep the `pages/` segment", () => {
  it("fetchDetails GETs <base>/pages/<id>/", async () => {
    const service = makeService();
    const get = vi.spyOn(service, "get").mockResolvedValue({ data: {} } as never);

    await service.fetchDetails("p1");

    expect(get).toHaveBeenCalledWith(`${BASE}/pages/p1/`, expect.anything());
  });

  it("fetchDescriptionBinary GETs <base>/pages/<id>/description/", async () => {
    const service = makeService();
    const get = vi.spyOn(service, "get").mockResolvedValue({ data: Buffer.from([1]) } as never);

    await service.fetchDescriptionBinary("p1");

    expect(get).toHaveBeenCalledWith(`${BASE}/pages/p1/description/`, expect.anything());
  });

  it("updatePageProperties PATCHes <base>/pages/<id>/", async () => {
    const service = makeService();
    const patch = vi.spyOn(service, "patch").mockResolvedValue({ data: {} } as never);

    await service.updatePageProperties("p1", { data: { name: "新标题" } });

    expect(patch).toHaveBeenCalledWith(`${BASE}/pages/p1/`, { name: "新标题" }, expect.anything());
  });

  it("updateDescriptionBinary PATCHes <base>/pages/<id>/description/", async () => {
    const service = makeService();
    const patch = vi.spyOn(service, "patch").mockResolvedValue({ data: {} } as never);

    await service.updateDescriptionBinary("p1", {
      description_binary: "AA==",
      description_html: "<p>x</p>",
      description_json: {},
    });

    expect(patch).toHaveBeenCalledWith(`${BASE}/pages/p1/description/`, expect.anything(), expect.anything());
  });

  it("fetchUserMentions GETs <base>/pages/<id>/mentions/", async () => {
    const service = makeService();
    const get = vi.spyOn(service, "get").mockResolvedValue({ data: [] } as never);

    await service.fetchUserMentions("p1");

    expect(get).toHaveBeenCalledWith(`${BASE}/pages/p1/mentions/`, expect.anything());
  });
});
