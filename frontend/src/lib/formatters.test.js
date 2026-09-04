import { describe, it, expect } from "vitest";
import { extractFileAttachments } from "./formatters";

describe("extractFileAttachments — user attachments", () => {
  it("serves a project-stored attachment as a project file (with thumbnail)", () => {
    const text = "look\n[Attached file: /home/x/xylocopa-projects/proj/.xylocopa/uploads/ab12_shot.png]";
    const [att] = extractFileAttachments(text, "proj", "USER");
    expect(att.resolvedUrl.startsWith("/api/files/proj/.xylocopa/uploads/ab12_shot.png")).toBe(true);
    expect(att.type).toBe("image");
  });

  it("keeps global uploads on /api/uploads", () => {
    const text = "[Attached file: /home/x/.xylocopa/uploads/ab12_doc.pdf]";
    const [att] = extractFileAttachments(text, "proj", "USER");
    expect(att.resolvedUrl.startsWith("/api/uploads/ab12_doc.pdf")).toBe(true);
  });

  it("uses metadata attachments when present", () => {
    const meta = { attachments: ["/home/x/xylocopa-projects/proj/.xylocopa/uploads/ab12_shot.png"] };
    const [att] = extractFileAttachments("", "proj", "USER", meta);
    expect(att.resolvedUrl.startsWith("/api/files/proj/")).toBe(true);
  });
});
