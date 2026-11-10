import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BoardHistory } from "@/lib/board/history";
import { SaveQueue } from "@/lib/board/saveQueue";
import {
    applySnapshots,
    containedKeys,
    duplicateShapes,
    IdRegistry,
    reorderObjects,
    shapeToData,
    sceneFromContent,
    syncConnectors,
    type Scene,
    type SceneObject,
    type ShapeObject,
} from "@/lib/board/scene";
import type { BoardSticker } from "@/lib/board/stickers";
import { defaultStyle, type BatchOp, type BoardShape } from "@/lib/board/types";

const stamp = "2026-09-28T10:00:00Z";

function sticker(id: number, x: number, y: number, z = 0): BoardSticker {
    return {
        id, project_id: 1, board_id: 1, body: `Стикер ${id}`, color: "#ffed8a", canvas_x: x, canvas_y: y, width: 200, height: 200, z_index: z,
        created_by_user_id: 1, created_by_username_snapshot: "k", created_by_display_name_snapshot: "К", task_ids: [], revision: 1, created_at: stamp, updated_at: stamp,
    };
}

function shape(id: number, patch: Partial<BoardShape>): BoardShape {
    return {
        id, board_id: 1, kind: "rect", x: 0, y: 0, w: 100, h: 60, points: null, start_ref: null, end_ref: null,
        style: defaultStyle(patch.kind ?? "rect"), label: "", z_index: 0, created_by_user_id: 1, revision: 1, created_at: stamp, updated_at: stamp,
        ...patch,
    };
}

function content() {
    return {
        stickers: [sticker(7, 0, 0, 1)],
        shapes: [
            shape(10, { x: 400, y: 0, w: 100, h: 200, z_index: 2 }),
            shape(11, {
                kind: "arrow", points: [[0, 0], [1, 1]], z_index: 3,
                start_ref: { type: "sticker", id: 7, anchor: "right" },
                end_ref: { type: "shape", id: 10, anchor: "left" },
            }),
        ],
    };
}

describe("сцена доски", () => {
    it("собирает стикеры и фигуры, выравнивает концы стрелок по узлам", () => {
        const registry = new IdRegistry();
        const scene = sceneFromContent(content(), registry);
        expect([...scene.keys()].sort()).toEqual(["f10", "f11", "s7"]);
        const arrow = scene.get("f11") as ShapeObject;
        expect(arrow.points).toEqual([[200, 100], [400, 100]]);
        expect(arrow.startRef).toEqual({ type: "sticker", key: "s7", anchor: "right" });
        expect(registry.idOf("f10")).toBe(10);
    });

    it("стрелка следует за узлом и отцепляется от удалённого", () => {
        const scene = sceneFromContent(content(), new IdRegistry());
        const moved: Scene = new Map(scene);
        const node = scene.get("f10")!;
        moved.set("f10", { ...node, y: 100 });
        const updates = syncConnectors(moved, new Set(["f10"]));
        expect(updates.get("f11")!.points).toEqual([[200, 100], [400, 200]]);

        const without: Scene = new Map(scene);
        without.delete("s7");
        const detached = syncConnectors(without, new Set(["s7"])).get("f11")!;
        expect(detached.startRef).toBeNull();
        expect(detached.points![0]).toEqual([200, 100]);
    });

    it("слои: на передний и задний план — общая шкала со стикерами", () => {
        const scene = sceneFromContent(content(), new IdRegistry());
        expect(reorderObjects(scene, ["s7"], "front").get("s7")!.z).toBe(4);
        expect(reorderObjects(scene, ["f11", "f10"], "back").get("f10")!.z).toBeLessThan(1);
        const back = reorderObjects(scene, ["f11", "f10"], "back");
        expect(back.get("f10")!.z).toBeLessThan(back.get("f11")!.z);
    });

    it("копия переводит ссылки внутри набора и отпускает внешние", () => {
        const registry = new IdRegistry();
        const scene = sceneFromContent(content(), registry);
        const objects = [scene.get("f10")!, scene.get("f11")!, scene.get("s7")!];
        const { shapes, stickers } = duplicateShapes(objects, 24, () => registry.newKey(), 10);
        expect(stickers[0].key).not.toBe("s7");
        expect(stickers[0].x).toBe(24);
        expect(stickers[0].z).toBe(10);
        const rect = shapes.find((item) => item.kind === "rect")!;
        const arrow = shapes.find((item) => item.kind === "arrow")!;
        expect(rect.x).toBe(424);
        expect(arrow.endRef).toEqual({ type: "shape", key: rect.key, anchor: "left" });
        expect(arrow.startRef).toEqual({ type: "sticker", key: stickers[0].key, anchor: "right" });
        const external = duplicateShapes([scene.get("f11")!], 24, () => registry.newKey(), 20);
        expect(external.shapes[0].startRef).toBeNull();
        expect(external.shapes[0].endRef).toBeNull();
    });

    it("снимки истории восстанавливают стикеры и удаляют фигуры", () => {
        const scene = sceneFromContent(content(), new IdRegistry());
        const snapshots = new Map<string, SceneObject | null>([
            ["f10", null],
            ["s7", { ...scene.get("s7")!, x: 50 }],
            ["s99", { ...scene.get("s7")!, key: "s99", x: 50 }],
        ]);
        const { scene: next, changed } = applySnapshots(scene, snapshots);
        expect(next.has("f10")).toBe(false);
        expect(next.get("s7")!.x).toBe(50);
        expect(next.has("s99")).toBe(true);
        expect([...changed].sort()).toEqual(["f10", "s7", "s99"]);
    });
});

describe("история", () => {
    it("отмена и повтор, лимит, сброс ветки повтора", () => {
        const history = new BoardHistory<number>(2);
        history.push({ before: new Map([["a", 1]]), after: new Map([["a", 2]]) });
        history.push({ before: new Map([["a", 2]]), after: new Map([["a", 3]]) });
        history.push({ before: new Map([["a", 3]]), after: new Map([["a", 4]]) });
        expect(history.size).toBe(2);
        expect(history.undo()!.get("a")).toBe(3);
        expect(history.redo()!.get("a")).toBe(4);
        history.undo();
        history.push({ before: new Map([["b", null]]), after: new Map([["b", 1]]) });
        expect(history.canRedo).toBe(false);
    });

    it("забывает объекты, ушедшие мимо истории", () => {
        const history = new BoardHistory<number>();
        history.push({ before: new Map([["a", 1]]), after: new Map([["a", 2]]) });
        history.push({ before: new Map([["a", 1], ["b", 1]]), after: new Map([["a", 2], ["b", 2]]) });
        history.forget(["a"]);
        expect(history.size).toBe(1);
        expect(history.undo()!.has("b")).toBe(true);
    });
});

describe("очередь сохранения", () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    function setup(send: (ops: BatchOp[]) => Promise<unknown>) {
        const registry = new IdRegistry();
        let scene = sceneFromContent(content(), registry);
        const failures: unknown[] = [];
        const queue = new SaveQueue({
            getScene: () => scene,
            registry,
            send: send as never,
            onFailure: (error) => failures.push(error),
        });
        return { registry, queue, failures, setScene: (next: Scene) => { scene = next; }, getScene: () => scene };
    }

    it("flush досылает тот же стикер, изменённый во время запроса, с новой ревизией", async () => {
        let firstReply!: (response: { shapes: []; stickers: BoardSticker[]; deleted_shape_ids: []; id_map: Record<string, number> }) => void;
        const send = vi.fn((ops: BatchOp[]) => {
            if (send.mock.calls.length === 1) return new Promise(resolve => { firstReply = resolve; });
            return Promise.resolve({ shapes: [], stickers: [{ ...sticker(7, 80, 0), revision: 3 }], deleted_shape_ids: [], id_map: {}, ops });
        });
        const { queue, getScene, setScene, failures } = setup(send);
        queue.markDirty(["s7"]);
        let finished = false;
        const flushing = queue.flushNow().then(() => { finished = true; });
        const next = new Map(getScene());
        next.set("s7", { ...next.get("s7")!, x: 80 });
        setScene(next);
        queue.markDirty(["s7"]);
        expect(finished).toBe(false);
        firstReply({ shapes: [], stickers: [{ ...sticker(7, 0, 0), revision: 2 }], deleted_shape_ids: [], id_map: {} });
        await flushing;
        expect(send).toHaveBeenCalledTimes(2);
        expect(send.mock.calls[1][0]).toMatchObject([{ op: "update_sticker", id: 7, revision: 2, data: { canvas_x: 80 } }]);
        expect(queue.pending).toBe(false);
        expect(failures).toEqual([]);
    });

    it("отмена создания во время flush досылает удаление полученного id", async () => {
        let firstReply!: (response: { shapes: []; stickers: []; deleted_shape_ids: []; id_map: Record<string, number> }) => void;
        const send = vi.fn((ops: BatchOp[]) => {
            if (send.mock.calls.length === 1) return new Promise(resolve => { firstReply = resolve; });
            return Promise.resolve({ shapes: [], stickers: [], deleted_shape_ids: [100], id_map: {}, ops });
        });
        const { registry, queue, getScene, setScene } = setup(send);
        const key = registry.newKey();
        const next = new Map(getScene());
        next.set(key, { ...next.get("f10")!, key });
        setScene(next);
        queue.markDirty([key]);
        const flushing = queue.flushNow();
        const undone = new Map(getScene());
        undone.delete(key);
        setScene(undone);
        queue.markDirty([key]);
        firstReply({ shapes: [], stickers: [], deleted_shape_ids: [], id_map: { [key]: 100 } });
        await flushing;
        expect(send.mock.calls.map(([ops]) => ops[0].op)).toEqual(["create_shape", "delete_shape"]);
        expect(send.mock.calls[1][0]).toEqual([{ op: "delete_shape", id: 100 }]);
        expect(registry.idOf(key)).toBeUndefined();
        expect(queue.pending).toBe(false);
    });

    it("flush останавливается с ошибкой, если цель привязки действительно не сохранена", async () => {
        const send = vi.fn(async () => ({ shapes: [], stickers: [], deleted_shape_ids: [], id_map: {} }));
        const { queue, getScene, setScene } = setup(send);
        const next = new Map(getScene());
        const arrow = next.get("f11") as ShapeObject;
        next.set("f11", { ...arrow, endRef: { type: "shape", key: "missing", anchor: "left" } });
        setScene(next);
        queue.markDirty(["f11"]);
        await expect(queue.flushNow()).rejects.toThrow("несохранённые привязки");
        expect(send).not.toHaveBeenCalled();
        queue.reset();
    });

    it("склеивает изменения за 300 мс в один пакет", async () => {
        const send = vi.fn(async (ops: BatchOp[]) => ({ shapes: [], deleted_shape_ids: [], stickers: [], id_map: {}, ops }));
        const { queue, setScene, getScene } = setup(send);
        const next = new Map(getScene());
        next.set("f10", { ...next.get("f10")!, x: 10 });
        setScene(next);
        queue.markDirty(["f10"]);
        queue.markDirty(["f10", "s7"]);
        expect(send).not.toHaveBeenCalled();
        await vi.advanceTimersByTimeAsync(300);
        expect(send).toHaveBeenCalledTimes(1);
        const ops = send.mock.calls[0][0] as BatchOp[];
        expect(ops.map((op) => op.op).sort()).toEqual(["update_shape", "update_sticker"]);
    });

    it("создание со ссылкой на новую фигуру — через client_id, затем id с сервера", async () => {
        const send = vi.fn(async (ops: BatchOp[]) => ({
            shapes: [], deleted_shape_ids: [], stickers: [],
            id_map: Object.fromEntries(ops.filter((op) => op.op === "create_shape").map((op, index) => [(op as { client_id: string }).client_id, 100 + index])),
        }));
        const { queue, registry, setScene, getScene } = setup(send);
        const next = new Map(getScene());
        const rect: ShapeObject = { ...(next.get("f10") as ShapeObject), key: "n1", x: 900 };
        const arrow: ShapeObject = { ...(next.get("f11") as ShapeObject), key: "n2", endRef: { type: "shape", key: "n1", anchor: "top" } };
        next.set("n1", rect);
        next.set("n2", arrow);
        setScene(next);
        queue.markDirty(["n2", "n1"]);
        await queue.flushNow();
        const ops = send.mock.calls[0][0] as BatchOp[];
        expect(ops[0]).toMatchObject({ op: "create_shape", client_id: "n1" });
        expect(ops[1]).toMatchObject({ op: "create_shape", client_id: "n2", data: { end_ref: { type: "shape", client_id: "n1", anchor: "top" } } });
        expect(registry.idOf("n1")).toBe(100);

        // Удаление уже сохранённой фигуры
        const after = new Map(getScene());
        after.delete("n1");
        setScene(after);
        queue.markDirty(["n1"]);
        await queue.flushNow();
        expect(send.mock.calls[1][0]).toEqual([{ op: "delete_shape", id: 100 }]);
        expect(registry.idOf("n1")).toBeUndefined();
    });

    it("в полёте один запрос, следующий пакет ждёт ответа", async () => {
        let release: () => void = () => undefined;
        const send = vi.fn(() => new Promise((resolve) => {
            release = () => resolve({ shapes: [], deleted_shape_ids: [], stickers: [], id_map: {} });
        }));
        const { queue } = setup(send);
        queue.markDirty(["f10"]);
        await vi.advanceTimersByTimeAsync(300);
        queue.markDirty(["s7"]);
        await vi.advanceTimersByTimeAsync(1000);
        expect(send).toHaveBeenCalledTimes(1);
        release();
        await vi.advanceTimersByTimeAsync(300);
        expect(send).toHaveBeenCalledTimes(2);
    });

    it("ошибка пакета — очередь пуста, редактор уведомлён", async () => {
        const send = vi.fn(async () => { throw new Error("409"); });
        const { queue, failures } = setup(send);
        queue.markDirty(["f10"]);
        await expect(queue.flushNow()).rejects.toThrow("409");
        expect(failures).toHaveLength(1);
        expect(queue.pending).toBe(false);
    });
});

describe("данные фигуры для сервера", () => {
    it("фигуре — рамка без точек, стрелке — точки и привязки без рамки; в обновлении нет вида", () => {
        const registry = new IdRegistry();
        const scene = sceneFromContent(content(), registry);
        const rect = shapeToData(scene.get("f10") as ShapeObject, registry, new Set(), "update");
        expect(rect).toEqual({ x: 400, y: 0, w: 100, h: 200, style: expect.any(Object), label: "", z_index: 2 });
        const arrow = shapeToData(scene.get("f11") as ShapeObject, registry, new Set(), "create");
        expect(arrow).toMatchObject({ kind: "arrow", points: [[200, 100], [400, 100]], start_ref: { type: "sticker", id: 7 }, end_ref: { type: "shape", id: 10 } });
        expect(arrow).not.toHaveProperty("w");
    });
});

describe("фигура-контейнер", () => {
    function containerScene(): Scene {
        return sceneFromContent(
            {
                stickers: [sticker(1, 20, 20), sticker(2, 900, 0)],
                shapes: [
                    shape(10, { x: 0, y: 0, w: 600, h: 400 }),
                    shape(11, { kind: "text", x: 300, y: 30, w: 120, h: 30, z_index: 1 }),
                    shape(12, { kind: "ellipse", x: 300, y: 200, w: 200, h: 150 }),
                    shape(13, { kind: "text", x: 350, y: 250, w: 60, h: 20 }),
                    shape(14, { kind: "line", points: [[250, 100], [280, 300]] }),
                    shape(15, { kind: "text", x: 580, y: 380, w: 80, h: 40 }),
                ],
            },
            new IdRegistry(),
        );
    }

    it("везёт всё, что целиком внутри, с вложенностью; торчащее наружу остаётся", () => {
        expect(containedKeys(containerScene(), ["f10"]).sort()).toEqual(["f11", "f12", "f13", "f14", "s1"]);
    });

    it("вложенная фигура везёт только своё; текст и стикер контейнером не являются", () => {
        const scene = containerScene();
        expect(containedKeys(scene, ["f12"])).toEqual(["f13"]);
        expect(containedKeys(scene, ["f11", "s1"])).toEqual([]);
    });
});

describe("смешанное дублирование и история сохранения", () => {
    it("копии стикера, фигуры и стрелки создаются одним пакетом с привязками к копиям", async () => {
        const registry = new IdRegistry();
        const original = sceneFromContent(content(), registry);
        const copied = duplicateShapes([...original.values()], 24, () => registry.newKey(), 10);
        let scene: Scene = new Map(original);
        const send = vi.fn(async (ops: BatchOp[]) => ({
            shapes: [], stickers: [], deleted_shape_ids: [],
            id_map: Object.fromEntries(ops.filter(op => op.op === "create_shape" || op.op === "create_sticker")
                .map((op, i) => [(op as {client_id: string}).client_id, 100 + i])),
        }));
        const queue = new SaveQueue({getScene: () => scene, registry, send, onFailure: error => { throw error; }});
        const added = [...copied.shapes, ...copied.stickers];
        scene = new Map([...scene, ...added.map(item => [item.key, item] as const)]);
        queue.markDirty(added.map(item => item.key));
        await queue.flushNow();
        expect(send).toHaveBeenCalledTimes(1);
        const ops = send.mock.calls[0][0];
        expect(ops.map(op => op.op).sort()).toEqual(["create_shape", "create_shape", "create_sticker"]);
        const arrow = ops.find(op => op.op === "create_shape" && op.data.kind === "arrow");
        expect(arrow).toMatchObject({data: {start_ref: {type: "sticker", client_id: copied.stickers[0].key}}});
        const history = new BoardHistory<SceneObject>();
        history.push({before: new Map(added.map(item => [item.key, null])), after: new Map(added.map(item => [item.key, item]))});
        scene = applySnapshots(scene, history.undo()!).scene;
        queue.markDirty(added.map(item => item.key));
        await queue.flushNow();
        expect(send.mock.calls[1][0].map(op => op.op).sort()).toEqual(["delete_shape", "delete_shape", "delete_sticker"]);
        scene = applySnapshots(scene, history.redo()!).scene;
        queue.markDirty(added.map(item => item.key));
        await queue.flushNow();
        expect(send.mock.calls[2][0].map(op => op.op).sort()).toEqual(["create_shape", "create_shape", "create_sticker"]);
        expect(scene.size).toBe(original.size + 3);
    });

    it("новые ревизии стикера берутся из ответа, а не старого снимка истории", async () => {
        const registry = new IdRegistry();
        let scene = sceneFromContent(content(), registry);
        let revision = 1;
        const send = vi.fn(async () => ({
            shapes: [], deleted_shape_ids: [], id_map: {},
            stickers: [{...sticker(7, 0, 0), revision: ++revision}],
        }));
        const queue = new SaveQueue({getScene: () => scene, registry, send, onFailure: () => undefined});
        queue.markDirty(["s7"]); await queue.flushNow();
        scene = new Map(scene); scene.set("s7", {...scene.get("s7")!, x: 80});
        queue.markDirty(["s7"]);
        expect(queue.buildOps().ops[0]).toMatchObject({op: "update_sticker", revision: 2});
        await queue.flushNow();
        scene.delete("s7"); queue.markDirty(["s7"]);
        expect(queue.buildOps().ops[0]).toMatchObject({op: "delete_sticker", revision: 3});
        await queue.flushNow();
    });
});
