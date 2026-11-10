import { describe, expect, it } from "vitest";
import { describePageContext, pageContext } from "@/lib/aris";

describe("Aris's board selection context", () => {
    it("passes complete saved refs only for the currently open board", () => {
        const selection = { boardId: 6, objects: [{ type: "sticker" as const, id: 1 }, { type: "shape" as const, id: 1 }] };
        const context = pageContext("/boards", "?board=6", null, selection);
        expect(context.board_selection).toEqual(selection.objects);
        expect(describePageContext(context)).toBe("Доска #6 · выделено: 2");
        expect(pageContext("/boards", "?board=7", null, selection)).not.toHaveProperty("board_selection");
        expect(pageContext("/my-tasks", "?board=6", null, selection)).not.toHaveProperty("board_selection");
    });
    it("does not truncate oversized selection into an ambiguous partial group", () => {
        const objects = Array.from({ length: 201 }, (_, i) => ({ type: "shape" as const, id: i + 1 }));
        expect(pageContext("/boards", "?board=6", null, { boardId: 6, objects })).not.toHaveProperty("board_selection");
        expect(pageContext("/boards", "?board=6", null, { boardId: 6, objects: [] })).not.toHaveProperty("board_selection");
        expect(pageContext("/boards", "?board=6", null, { boardId: 6, objects: [{ type: "shape", id: -1 }] })).not.toHaveProperty("board_selection");
    });
});
