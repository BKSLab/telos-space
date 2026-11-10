import { MAX_COORDINATE, unionBoxes } from "@/lib/board/geometry";
import { connectorPoints, nodeLookup, sortedObjects, type Scene } from "@/lib/board/scene";
import { normalizeStickerColor, type BoardStickerCreateInput } from "@/lib/board/stickers";
import { isConnectorKind, type ShapeData, type ServerRef } from "@/lib/board/types";
import type { BoardGrouping } from "@/lib/board/group";

export type TemplateItem =
    | { type: "shape"; client_id: string; data: ShapeData & { kind: NonNullable<ShapeData["kind"]> } }
    | { type: "sticker"; client_id: string; data: BoardStickerCreateInput };
export interface BoardTemplate {
    version: 1;
    id: string;
    name: string;
    items: TemplateItem[];
    groups: { id: string; name: string; members: string[] }[];
    locked: string[];
}
export type TemplateCreateOp =
    | { op: "create_shape"; client_id: string; data: ShapeData }
    | { op: "create_sticker"; client_id: string; data: BoardStickerCreateInput };
export interface TemplateInsertion {
    operations: TemplateCreateOp[];
    groups: BoardTemplate["groups"];
    locked: string[];
}

const MAX_ITEMS = 200;
const MAX_PAYLOAD_BYTES = 512_000;
const coordinate = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= MAX_COORDINATE;
const nonempty = (value: unknown, limit: number): value is string => typeof value === "string" && !value.includes(String.fromCharCode(0)) && value.trim().length > 0 && value.length <= limit;
const identifier = (value: unknown): value is string => nonempty(value, 64) && /^[A-Za-z0-9_-]+$/.test(value);
const anchors = new Set(["top", "right", "bottom", "left", "center"]);
const colors = new Set(["neutral", "yellow", "blue", "green", "red", "violet", "none"]);

export function validateTemplate(template: BoardTemplate): void {
    const fail = () => { throw new Error("Шаблон повреждён или превышает допустимый размер."); };
    const fields = (value: object, allowed: string[]) => {
        if (Object.keys(value).some((key) => !allowed.includes(key))) fail();
    };
    if (!template || template.version !== 1 || !identifier(template.id) || !nonempty(template.name, 120)
        || !Array.isArray(template.items) || template.items.length < 1 || template.items.length > MAX_ITEMS
        || !Array.isArray(template.groups) || !Array.isArray(template.locked)) fail();
    fields(template, ["version", "id", "name", "items", "groups", "locked"]);
    if (new TextEncoder().encode(JSON.stringify(template)).length > MAX_PAYLOAD_BYTES) fail();
    const items = new Map<string, TemplateItem>();
    for (const item of template.items) {
        if (!item || !identifier(item.client_id) || items.has(item.client_id) || !item.data) fail();
        fields(item, ["type", "client_id", "data"]);
        items.set(item.client_id, item);
        const data = item.data;
        if (data.z_index !== undefined && (!Number.isSafeInteger(data.z_index) || Math.abs(data.z_index) > 1_000_000_000)) fail();
        if (item.type === "sticker") {
            const sticker = item.data;
            fields(sticker, ["body", "color", "task_ids", "canvas_x", "canvas_y", "width", "height", "z_index"]);
            if (!nonempty(sticker.body, 2000) || !/^#[0-9a-f]{6}$/i.test(sticker.color)
                || !coordinate(sticker.canvas_x) || !coordinate(sticker.canvas_y)
                || !Array.isArray(sticker.task_ids) || sticker.task_ids.length !== 0
                || !coordinate(sticker.width) || !coordinate(sticker.height)
                || sticker.width! < 160 || sticker.width! > 520 || sticker.height! < 160 || sticker.height! > 520) fail();
        } else if (item.type === "shape") {
            const shape = item.data;
            fields(shape, ["kind", "x", "y", "w", "h", "points", "start_ref", "end_ref", "style", "label", "z_index"]);
            if (!["rect", "ellipse", "diamond", "text", "arrow", "line"].includes(shape.kind)
                || typeof shape.label !== "string" || shape.label.length > 2000 || shape.label.includes(String.fromCharCode(0))) fail();
            const style = shape.style;
            if (!style || !colors.has(style.stroke) || !colors.has(style.fill)
                || ![1, 2, 4].includes(style.stroke_width) || !["solid", "dashed"].includes(style.dash)
                || !["straight", "orthogonal"].includes(style.route)
                || !["none", "arrow"].includes(style.start_head) || !["none", "arrow"].includes(style.end_head)
                || !["s", "m", "l"].includes(style.font_size) || !["left", "center"].includes(style.text_align)) fail();
            fields(style, ["stroke", "fill", "stroke_width", "dash", "route", "start_head", "end_head", "font_size", "text_align"]);
            if (isConnectorKind(shape.kind)) {
                if (!Array.isArray(shape.points) || shape.points.length < 2 || shape.points.length > 50
                    || shape.points.some((point) => !Array.isArray(point) || point.length !== 2 || !point.every(coordinate))) fail();
            } else if (![shape.x, shape.y, shape.w, shape.h].every(coordinate)
                || shape.w! < 1 || shape.h! < 1 || shape.w! > 20_000 || shape.h! > 20_000) fail();
        } else fail();
    }
    const validateRef = (ref: ServerRef | null | undefined) => {
        if (!ref) return;
        fields(ref, ["type", "client_id", "anchor"]);
        const target = items.get(ref.client_id ?? "");
        if (ref.id !== undefined || !anchors.has(ref.anchor) || !target || ref.type !== target.type
            || (target.type === "shape" && isConnectorKind(target.data.kind))) fail();
    };
    template.items.forEach((item) => {
        if (item.type === "shape") { validateRef(item.data.start_ref); validateRef(item.data.end_ref); }
    });
    const groupIds = new Set<string>(), grouped = new Set<string>();
    for (const group of template.groups) {
        if (!group || !identifier(group.id) || !nonempty(group.name, 120) || groupIds.has(group.id)
            || !Array.isArray(group.members) || group.members.length < 2) fail();
        fields(group, ["id", "name", "members"]);
        groupIds.add(group.id);
        for (const key of group.members) {
            if (!items.has(key) || grouped.has(key)) fail();
            grouped.add(key);
        }
    }
    if (new Set(template.locked).size !== template.locked.length || template.locked.some((key) => !items.has(key))) fail();
}

/** A portable snapshot: no source object IDs, task links, authors or board ID. */
export function createSelectionTemplate(
    scene: Scene, keys: Iterable<string>, name: string, id: string, state: BoardGrouping = { groups: [], locked: [] },
): BoardTemplate {
    const selected = new Set(keys);
    const objects = sortedObjects(scene).filter((object) => selected.has(object.key));
    if (!objects.length) throw new Error("Выделите объекты для шаблона.");
    const box = unionBoxes(objects)!;
    const ids = new Map(objects.map((object, index) => [object.key, `t${index}`]));
    const lookup = nodeLookup(scene);
    const items: TemplateItem[] = objects.map((object, index) => {
        const client_id = ids.get(object.key)!;
        if (object.type === "sticker") {
            return { type: "sticker", client_id, data: {
                body: object.sticker.body, color: normalizeStickerColor(object.sticker.color), task_ids: [],
                canvas_x: object.x - box.x, canvas_y: object.y - box.y,
                width: object.w, height: object.h, z_index: index,
            } };
        }
        const remap = (ref: typeof object.startRef): ServerRef | null =>
            ref && ids.has(ref.key) ? { type: ref.type, client_id: ids.get(ref.key)!, anchor: ref.anchor } : null;
        const common = { kind: object.kind, style: { ...object.style }, label: object.label, z_index: index, start_ref: null, end_ref: null };
        return { type: "shape", client_id, data: isConnectorKind(object.kind)
            ? { ...common, points: connectorPoints(object, lookup).map(([x, y]) => [x - box.x, y - box.y]),
                start_ref: remap(object.startRef), end_ref: remap(object.endRef) }
            : { ...common, x: object.x - box.x, y: object.y - box.y, w: object.w, h: object.h } };
    });
    const template: BoardTemplate = {
        version: 1, id, name: name.trim(), items,
        groups: state.groups.filter((group) => group.members.every((key) => ids.has(key)))
            .map((group, index) => ({ ...group, id: `g${index}`, members: group.members.map((key) => ids.get(key)!) })),
        locked: state.locked.filter((key) => ids.has(key)).map((key) => ids.get(key)!),
    };
    validateTemplate(template);
    return template;
}

/** Parent submits these operations in ONE atomic mixed batch and maps returned IDs. */
export function instantiateTemplate(
    template: BoardTemplate, origin: { x: number; y: number }, newId: () => string, zStart: number,
): TemplateInsertion {
    validateTemplate(template);
    if (!coordinate(origin.x) || !coordinate(origin.y) || !Number.isSafeInteger(zStart)) {
        throw new Error("Укажите допустимую точку вставки.");
    }
    const ids = new Map(template.items.map((item) => [item.client_id, newId()]));
    const groupIds = template.groups.map(() => newId());
    const generated = [...ids.values(), ...groupIds];
    if (new Set(generated).size !== generated.length || generated.some((id) => !identifier(id))) {
        throw new Error("Не удалось назначить уникальные идентификаторы.");
    }
    const remap = (ref: ServerRef | null | undefined): ServerRef | null => ref
        ? { type: ref.type, client_id: ids.get(ref.client_id!)!, anchor: ref.anchor } : null;
    const operations: TemplateCreateOp[] = template.items.map((item, index) => {
        const client_id = ids.get(item.client_id)!;
        if (item.type === "sticker") {
            return { op: "create_sticker", client_id, data: { ...item.data, task_ids: [],
                canvas_x: item.data.canvas_x + origin.x, canvas_y: item.data.canvas_y + origin.y, z_index: zStart + index } };
        }
        const data = item.data;
        return { op: "create_shape", client_id, data: isConnectorKind(data.kind)
            ? { ...data, style: { ...data.style }, points: data.points!.map(([x, y]) => [x + origin.x, y + origin.y]),
                start_ref: remap(data.start_ref), end_ref: remap(data.end_ref), z_index: zStart + index }
            : { ...data, style: { ...data.style }, x: data.x! + origin.x, y: data.y! + origin.y, z_index: zStart + index } };
    });
    // Check translated coordinates before invoking the parent's mutation callback.
    validateTemplate({ ...template, items: operations.map((op) => op.op === "create_shape"
        ? { type: "shape", client_id: op.client_id, data: op.data as Extract<TemplateItem, { type: "shape" }>["data"] }
        : { type: "sticker", client_id: op.client_id, data: op.data }), groups: [], locked: [] });
    return {
        operations,
        groups: template.groups.map((group, index) => ({ ...group, id: groupIds[index], members: group.members.map((key) => ids.get(key)!) })),
        locked: template.locked.map((key) => ids.get(key)!),
    };
}
