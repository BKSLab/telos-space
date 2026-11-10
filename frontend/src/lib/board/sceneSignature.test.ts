import { describe, expect, it } from "vitest";
import { IdRegistry, sceneFromContent } from "@/lib/board/scene";
import { sceneSignature } from "@/lib/board/sceneSignature";
import type { BoardSticker } from "@/lib/board/stickers";

const sticker: BoardSticker = {
    id: 7, project_id: 1, board_id: 1, body: "Заметка", color: "#fff19a",
    canvas_x: 0, canvas_y: 0, width: 230, height: 230, z_index: 0, task_ids: [102, 101],
    created_by_user_id: 1, created_by_username_snapshot: "tester",
    created_by_display_name_snapshot: "Тест", revision: 1,
    created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z",
};

describe("сравнение сцены при polling", () => {
    it("порядок связей из PostgreSQL и ревизия собственного сохранения не меняют содержимое", () => {
        const registry = new IdRegistry();
        const local = sceneFromContent({ stickers: [sticker], shapes: [] }, registry);
        const remote = sceneFromContent({
            stickers: [{ ...sticker, task_ids: [101, 102], revision: 2, updated_at: "2026-10-09T00:01:00Z" }],
            shapes: [],
        }, registry);
        expect(sceneSignature(local)).toBe(sceneSignature(remote));
        expect(sticker.task_ids).toEqual([102, 101]);
    });

    it("изменение набора связей или текста остаётся внешним изменением", () => {
        const registry = new IdRegistry();
        const local = sceneFromContent({ stickers: [sticker], shapes: [] }, registry);
        for (const changed of [{ ...sticker, task_ids: [101] }, { ...sticker, body: "Другой текст" }]) {
            const remote = sceneFromContent({ stickers: [changed], shapes: [] }, registry);
            expect(sceneSignature(local)).not.toBe(sceneSignature(remote));
        }
    });
});
