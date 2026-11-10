import { describe, expect, it } from "vitest";
import type { ProjectMember, Task } from "@/lib/types";
import {
    findAvailableStickerPosition,
    limitStickerTaskResults,
    mergeStickerTaskResults,
    normalizeStickerInput,
    normalizeStickerPosition,
    resolveStickerAuthor,
    searchStickerTasks,
    stickerAuthorLabel,
    stickerHasChanges,
    STICKER_SIZE_PRESETS,
    type BoardSticker,
} from "@/lib/board/stickers";

const sticker: BoardSticker = {
    id: 1,
    project_id: 3,
    body: "Согласовать API",
    color: "#fff19a",
    canvas_x: 40,
    canvas_y: 40,
    created_by_user_id: 7,
    created_by_username_snapshot: "old-login",
    created_by_display_name_snapshot: "Старое Имя",
    task_ids: [2, 1],
    revision: 4,
    created_at: "2026-09-04T08:00:00Z",
    updated_at: "2026-09-04T08:00:00Z",
};

const member: ProjectMember = {
    id: 12,
    project_id: 3,
    role: "MEMBER",
    created_at: "2026-09-01T08:00:00Z",
    user: {
        id: 7,
        username: "i.ivanov",
        last_name: "Иванов", first_name: "Иван", middle_name: null, has_avatar: false,
    },
};

describe("project board stickers", () => {
    it("uses the current project member identity before the snapshot", () => {
        const author = resolveStickerAuthor(sticker, [member]);

        expect(author.kind).toBe("current");
        expect(author.displayName).toBe("Иванов Иван");
        expect(stickerAuthorLabel(author)).toContain("@i.ivanov");
    });

    it("falls back to immutable snapshots for a former member", () => {
        const author = resolveStickerAuthor(sticker, []);

        expect(author.kind).toBe("former");
        expect(author.initials).toBe("СИ");
        expect(stickerAuthorLabel(author)).toContain("бывший участник");
    });

    it("normalizes text and unique task links", () => {
        expect(normalizeStickerInput({
            body: "  Текст  ",
            color: "#7C1623",
            task_ids: [3, 3, 1],
        })).toEqual({ body: "Текст", color: "#7c1623", task_ids: [3, 1] });
    });

    it("compares task links independent of their order", () => {
        expect(stickerHasChanges(sticker, {
            body: sticker.body,
            color: sticker.color,
            task_ids: [1, 2],
        })).toBe(false);
        expect(stickerHasChanges(sticker, {
            body: sticker.body,
            color: "#d8f5b5",
            task_ids: [1, 2],
        })).toBe(true);
        // Тот же цвет в другом регистре — не изменение.
        expect(stickerHasChanges(sticker, {
            body: sticker.body,
            color: "#FFF19A",
            task_ids: [1, 2],
        })).toBe(false);
    });

    it("limits the compact task picker to five results", () => {
        const tasks = Array.from({ length: 8 }, (_, index) => ({
            id: index + 1,
            key: `CRM-${index + 1}`,
            title: `Задача ${index + 1}`,
        })) as Task[];

        expect(limitStickerTaskResults(tasks).map((task) => task.id)).toEqual([1, 2, 3, 4, 5]);
    });

    it("finds tasks locally from the first characters of a query", () => {
        const tasks = [
            {
                id: 1,
                key: "CRM-1",
                title: "Помощь в формулировке задачи",
                description_md: null,
                last_comment: null,
                assignee: null,
            },
            {
                id: 2,
                key: "CRM-2",
                title: "Пересмотреть календарь",
                description_md: "Проверить поля формы",
                last_comment: null,
                assignee: null,
            },
        ] as Task[];

        expect(searchStickerTasks(tasks, "по").map((task) => task.id)).toEqual([1, 2]);
        expect(searchStickerTasks(tasks, "поля формы").map((task) => task.id)).toEqual([2]);
    });

    it("merges local and server task results without duplicates", () => {
        const first = { id: 1, key: "CRM-1", title: "Первая" } as Task;
        const second = { id: 2, key: "CRM-2", title: "Вторая" } as Task;

        expect(mergeStickerTaskResults([first], [first, second])).toEqual([first, second]);
    });

    it("normalizes persisted canvas coordinates", () => {
        expect(normalizeStickerPosition({ x: 12.345, y: Number.POSITIVE_INFINITY })).toEqual({
            canvas_x: 12.3,
            canvas_y: 0,
        });
    });

    it("provides three size presets and clamps free resize bounds", () => {
        expect(STICKER_SIZE_PRESETS.small.width).toBeLessThan(STICKER_SIZE_PRESETS.medium.width);
        expect(STICKER_SIZE_PRESETS.large.width).toBeGreaterThan(STICKER_SIZE_PRESETS.medium.width);
        expect(normalizeStickerPosition({ x: 10, y: 20, width: 50, height: 900 })).toMatchObject({
            width: 160,
            height: 520,
        });
    });

    it("places a new sticker beside an occupied viewport center", () => {
        const next = findAvailableStickerPosition([sticker], { x: 40, y: 40 });

        expect(next).not.toEqual({ canvas_x: 40, canvas_y: 40 });
        expect(Math.abs(next.canvas_x - sticker.canvas_x)).toBeGreaterThanOrEqual(258);
    });
});
