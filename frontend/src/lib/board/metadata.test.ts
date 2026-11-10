import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "./editorTemplate.fixture.json";
import type { StoredBoardTemplate } from "./metadata";
import { instantiateTemplate } from "./template";
import { api, endpoints } from "@/lib/api";
import { IdRegistry } from "./scene";
import { createSelectionTemplate } from "./template";
import { scene, shape, sticker } from "./editorFixtures.test-support";
import { createProjectFromBoard, decodeMetadata, decodeTemplate, editorMetadataEndpoint, encodeMetadata, encodeTemplate, loadEditorMetadata, saveEditorMetadata, templateBatchRequest } from "./metadata";

afterEach(() => vi.restoreAllMocks());
describe("board metadata adapter", () => {
    it("persists server IDs and maps to a fresh registry after reload", () => {
        const source = scene(shape("n1"), sticker(9));
        const registry = new IdRegistry(); registry.bind("n1", 51);
        const stored = encodeMetadata(source, registry, {
            groups: [{ id: "group", name: "Группа", members: ["n1", "s9"] }], locked: ["n1"],
        }, []);
        expect(stored.groups[0].members).toEqual([{ type: "shape", id: 51 }, { type: "sticker", id: 9 }]);
        const loaded = decodeMetadata(JSON.parse(JSON.stringify(stored)), new IdRegistry());
        expect(loaded.groups[0].members).toEqual(["f51", "s9"]);
        expect(loaded.locked).toEqual(["f51"]);
    });
    it("does not save an unsaved or deleted object reference", () => {
        const state = { groups: [], locked: ["n1"] };
        expect(() => encodeMetadata(scene(shape("n1")), new IdRegistry(), state, [])).toThrow("сохранения");
        expect(() => encodeMetadata(new Map(), new IdRegistry(), state, [])).toThrow();
    });
    it("roundtrips mixed templates with group and lock metadata", () => {
        const a = shape("a");
        const arrow = { ...shape("arrow", 100, 40, 100, 150, "arrow"),
            points: [[100, 40], [200, 215]] as [number, number][],
            startRef: { type: "shape" as const, key: "a", anchor: "right" as const },
            endRef: { type: "sticker" as const, key: "s1", anchor: "left" as const } };
        const source = scene(a, arrow, sticker());
        const template = createSelectionTemplate(source, source.keys(), "Схема", "t",
            { groups: [{ id: "g", name: "Узлы", members: ["a", "s1"] }], locked: ["s1"] });
        const stored = encodeTemplate(template);
        const loaded = decodeTemplate(JSON.parse(JSON.stringify(stored)));
        expect(loaded).toEqual(template);
        const object = stored.objects.find((item) => item.type === "shape" && item.data.kind === "arrow")!;
        expect(object.data).toMatchObject({ end_ref: { object_id: "t2", anchor: "left" } });
    });
    it("fills schema defaults when server templates omit shape style", () => {
        const loaded = decodeTemplate({ id: "t", name: "Схема", objects: [{ type: "shape", object_id: "box",
            data: { kind: "rect", x: 0, y: 0, w: 1, h: 1, label: "", z_index: 0, style: { fill: null, dash: "dashed" } } }] });
        expect(loaded.items[0]).toMatchObject({ data: { style: { fill: "none", stroke: "neutral", dash: "dashed" } } });
    });
    it("passes insertion groups and locks with operations in one batch body", () => {
        const insertion = { operations: [], groups: [{ id: "g", name: "Группа", members: ["n1", "n2"] }], locked: ["n2"] };
        expect(templateBatchRequest(insertion, 3)).toEqual({ ops: [], editor_insert: {
            expected_revision: 3, groups: insertion.groups, locked: ["n2"],
        } });
        expect(() => templateBatchRequest(insertion, 0)).toThrow();
    });
    it("decodes the same wire fixture verified by PostgreSQL and inserts mixed refs", () => {
        const template = decodeTemplate(fixture as StoredBoardTemplate);
        let index = 0;
        const inserted = instantiateTemplate(template, { x: 100, y: 200 }, () => `n${index++}`, 0);
        expect(inserted.groups[0].members).toEqual(["n0", "n1"]);
        expect(inserted.locked).toEqual(["n1"]);
        expect(inserted.operations[2]).toMatchObject({ data: { end_ref: { type: "sticker", client_id: "n1" } } });
    });
    it("uses the ordinary session API and distinct project/free-board URLs", async () => {
        expect(editorMetadataEndpoint(null, 7)).toBe("/api/v1/boards/7/editor-metadata");
        expect(editorMetadataEndpoint(3, 7)).toBe("/api/v1/projects/3/boards/7/editor-metadata");
        const metadata = { version: 1 as const, groups: [], locked: [], templates: [] };
        const result = { board_id: 7, revision: 2, metadata };
        const get = vi.spyOn(api, "get").mockResolvedValue(result);
        const put = vi.spyOn(api, "put").mockResolvedValue(result);
        await loadEditorMetadata(null, 7);
        await saveEditorMetadata(3, 7, 1, metadata);
        expect(get).toHaveBeenCalledWith(editorMetadataEndpoint(null, 7));
        expect(put).toHaveBeenCalledWith(editorMetadataEndpoint(3, 7), { expected_revision: 1, metadata });
        const post = vi.spyOn(api, "post").mockResolvedValue({});
        await createProjectFromBoard(7, 2, { key: "TEST", name: "Проект" });
        expect(post).toHaveBeenCalledWith(`${endpoints.board(null, 7)}/create-project`,
            { expected_revision: 2, project: { key: "TEST", name: "Проект" } });
    });
});
