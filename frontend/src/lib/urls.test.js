import { describe, it, expect } from "vitest";
import { RE_PROJECTS_PATH, RE_UPLOADS_PATH, fileUrl, fileUrlToThumbUrl, API_FILES_PREFIX, attachmentUrl } from "./urls";

describe("RE_PROJECTS_PATH", () => {
  it("extracts project and rel-path for a project-scoped upload", () => {
    const absPath = "/home/x/xylocopa-projects/proj/.xylocopa/uploads/a_b.png";
    const m = absPath.match(RE_PROJECTS_PATH);
    expect(m).not.toBeNull();
    expect(m[1]).toBe("proj");
    expect(m[2]).toBe(".xylocopa/uploads/a_b.png");
  });

  it("extracts project and rel-path for a regular project file", () => {
    const absPath = "/home/x/xylocopa-projects/myproj/src/main.py";
    const m = absPath.match(RE_PROJECTS_PATH);
    expect(m).not.toBeNull();
    expect(m[1]).toBe("myproj");
    expect(m[2]).toBe("src/main.py");
  });

  it("matches legacy agenthive-projects paths", () => {
    const absPath = "/home/x/agenthive-projects/old/.xylocopa/uploads/f.png";
    const m = absPath.match(RE_PROJECTS_PATH);
    expect(m).not.toBeNull();
    expect(m[1]).toBe("old");
    expect(m[2]).toBe(".xylocopa/uploads/f.png");
  });
});

describe("fileUrl for project-scoped uploads", () => {
  it("builds /api/files/<project>/... for a .xylocopa/uploads path", () => {
    const url = fileUrl("proj", ".xylocopa/uploads/a_b.png");
    // fileUrl appends ?token=... when auth is set; strip query to check path
    const path = url.split("?")[0];
    expect(path).toBe("/api/files/proj/.xylocopa/uploads/a_b.png");
  });

  it("produces a URL convertible to a thumb URL", () => {
    const url = fileUrl("proj", ".xylocopa/uploads/a_b.png").split("?")[0];
    const thumb = fileUrlToThumbUrl(url);
    expect(thumb.startsWith("/api/thumbs/")).toBe(true);
  });
});

describe("attachmentUrl", () => {
  it("routes a project-stored attachment to /api/files/<project>/.xylocopa/uploads/...", () => {
    const url = attachmentUrl("/home/x/xylocopa-projects/proj/.xylocopa/uploads/ab12_shot.png");
    expect(url.startsWith("/api/files/proj/.xylocopa/uploads/ab12_shot.png")).toBe(true);
  });

  it("routes a global upload (~/.xylocopa/uploads) to /api/uploads/<name>", () => {
    const url = attachmentUrl("/home/x/.xylocopa/uploads/ab12_shot.png");
    expect(url.startsWith("/api/uploads/ab12_shot.png")).toBe(true);
  });

  it("falls back to /api/uploads for a bare filename", () => {
    expect(attachmentUrl("ab12_shot.png").startsWith("/api/uploads/ab12_shot.png")).toBe(true);
  });
});
