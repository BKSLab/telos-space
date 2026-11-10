import { resolveAnchorPoint, type AnchorNode } from "@/lib/board/anchors";
import { boundsOfPoints, clampCoordinate, type Box } from "@/lib/board/geometry";
import { preservePolyline, routeConnector } from "@/lib/board/routing";
import {
    MAX_STICKER_SIZE,
    MIN_STICKER_SIZE,
    STICKER_NODE_HEIGHT,
    STICKER_NODE_WIDTH,
    type BoardSticker,
} from "@/lib/board/stickers";
import {
    isConnectorKind,
    type Anchor,
    type BoardContent,
    type BoardShape,
    type Point,
    type ServerRef,
    type ShapeData,
    type ShapeKind,
    type ShapeStyle,
} from "@/lib/board/types";

/**
 * Сцена доски — все объекты одного «мира»: стикеры и фигуры с общей шкалой слоёв.
 * Ключ объекта стабилен на всё время жизни редактора: «s12» — стикер 12,
 * «f55» — фигура, пришедшая с сервера, «n3» — фигура, созданная здесь и, возможно,
 * ещё не сохранённая. Серверные id фигур живут в IdRegistry, а не в объектах:
 * так снимки истории не зависят от того, успел ли сервер ответить.
 */

export interface LocalRef {
    type: "sticker" | "shape";
    key: string;
    anchor: Anchor;
}

interface ObjectBase {
    key: string;
    x: number;
    y: number;
    w: number;
    h: number;
    z: number;
}

export interface StickerObject extends ObjectBase {
    type: "sticker";
    id: number;
    sticker: BoardSticker;
}

export interface ShapeObject extends ObjectBase {
    type: "shape";
    kind: ShapeKind;
    points: Point[] | null;
    startRef: LocalRef | null;
    endRef: LocalRef | null;
    style: ShapeStyle;
    label: string;
}

export type SceneObject = StickerObject | ShapeObject;
export type Scene = Map<string, SceneObject>;

export const STICKER_LIMITS = { minW: MIN_STICKER_SIZE, minH: MIN_STICKER_SIZE, maxW: MAX_STICKER_SIZE, maxH: MAX_STICKER_SIZE };
export const SHAPE_LIMITS = { minW: 8, minH: 8, maxW: 20_000, maxH: 20_000 };

export function stickerKey(id: number): string {
    return `s${id}`;
}

/** Стрелка или линия. Не type guard: «не стрелка» — это и стикер, и обычная фигура. */
export function isConnector(object: SceneObject): boolean {
    return object.type === "shape" && isConnectorKind(object.kind);
}

/** Соответствие ключей фигур и их серверных id. */
export class IdRegistry {
    private idByKey = new Map<string, number>();
    private keyById = new Map<number, string>();
    private counter = 0;
    private stickerKeyById = new Map<number, string>();

    newKey(): string {
        this.counter += 1;
        return `n${this.counter}`;
    }

    newStickerKey(): string {
        this.counter += 1;
        return `snew${this.counter}`;
    }

    keyForSticker(id: number): string {
        const key = this.stickerKeyById.get(id) ?? stickerKey(id);
        this.bind(key, id);
        return key;
    }

    bind(key: string, id: number): void {
        this.idByKey.set(key, id);
        if (key.startsWith("s")) this.stickerKeyById.set(id, key);
        else this.keyById.set(id, key);
    }

    unbind(key: string): void {
        const id = this.idByKey.get(key);
        this.idByKey.delete(key);
        if (id !== undefined) {
            if (key.startsWith("s")) this.stickerKeyById.delete(id);
            else this.keyById.delete(id);
        }
    }

    idOf(key: string): number | undefined {
        return this.idByKey.get(key);
    }

    /** Ключ фигуры по серверному id; незнакомый id получает ключ «f<id>». */
    keyFor(id: number): string {
        const known = this.keyById.get(id);
        if (known) return known;
        const key = `f${id}`;
        this.bind(key, id);
        return key;
    }
}

export function stickerObject(sticker: BoardSticker, key = stickerKey(sticker.id)): StickerObject {
    return {
        type: "sticker",
        key,
        id: sticker.id,
        sticker,
        x: sticker.canvas_x,
        y: sticker.canvas_y,
        w: sticker.width ?? STICKER_NODE_WIDTH,
        h: sticker.height ?? STICKER_NODE_HEIGHT,
        z: sticker.z_index ?? 0,
    };
}

function refFromServer(ref: ServerRef | null, registry: IdRegistry): LocalRef | null {
    if (!ref || ref.id === undefined || ref.id === null) return null;
    return {
        type: ref.type,
        key: ref.type === "sticker" ? registry.keyForSticker(ref.id) : registry.keyFor(ref.id),
        anchor: ref.anchor,
    };
}

export function shapeFromServer(shape: BoardShape, registry: IdRegistry): ShapeObject {
    const object = shapeFromServerRaw(shape, registry);
    // Рамку стрелки считаем по её точкам сами — не зависим от округлений сервера.
    return isConnectorKind(object.kind) && object.points && object.points.length >= 2
        ? withConnectorGeometry(object, object.points)
        : object;
}

function shapeFromServerRaw(shape: BoardShape, registry: IdRegistry): ShapeObject {
    return {
        type: "shape",
        key: registry.keyFor(shape.id),
        kind: shape.kind,
        x: shape.x,
        y: shape.y,
        w: shape.w,
        h: shape.h,
        z: shape.z_index,
        points: shape.points ? shape.points.map(([x, y]) => [x, y] as Point) : null,
        startRef: refFromServer(shape.start_ref, registry),
        endRef: refFromServer(shape.end_ref, registry),
        style: shape.style,
        label: shape.label ?? "",
    };
}

export function sceneFromContent(content: Pick<BoardContent, "stickers" | "shapes">, registry: IdRegistry): Scene {
    const scene: Scene = new Map();
    for (const sticker of content.stickers) {
        const key = registry.keyForSticker(sticker.id);
        scene.set(key, stickerObject(sticker, key));
    }
    for (const shape of content.shapes) {
        const object = shapeFromServer(shape, registry);
        scene.set(object.key, object);
    }
    // Концы стрелок могли разъехаться (узел двигали, пока стрелка была без изменений) — выравниваем.
    for (const [key, updated] of syncConnectors(scene)) scene.set(key, updated);
    return scene;
}

/**
 * Серверное представление ссылки. createdInBatch — ключи фигур, создаваемых
 * в том же пакете (на них ссылаются по client_id). null — ссылку пока не выразить.
 */
export function refToServer(ref: LocalRef, registry: IdRegistry, createdInBatch: Set<string>): ServerRef | null | "defer" {
    const id = registry.idOf(ref.key);
    if (id !== undefined) return { type: ref.type, id, anchor: ref.anchor };
    if (createdInBatch.has(ref.key)) return { type: ref.type, client_id: ref.key, anchor: ref.anchor };
    return "defer";
}

export function objectBox(object: SceneObject): Box {
    return { x: object.x, y: object.y, w: object.w, h: object.h };
}

/** Узлы, к которым можно цепляться: всё, кроме стрелок и линий. */
export function anchorNodes(scene: Scene, overrides?: Map<string, Box>): AnchorNode[] {
    const nodes: AnchorNode[] = [];
    for (const object of scene.values()) {
        if (isConnector(object)) continue;
        nodes.push({
            key: object.key,
            kind: object.type === "sticker" ? "sticker" : object.kind,
            box: overrides?.get(object.key) ?? objectBox(object),
            z: object.z,
        });
    }
    return nodes;
}

export function nodeLookup(scene: Scene, overrides?: Map<string, Box>): (key: string) => AnchorNode | undefined {
    return (key) => {
        const object = scene.get(key);
        if (!object || isConnector(object)) return undefined;
        return {
            key,
            kind: object.type === "sticker" ? "sticker" : object.kind,
            box: overrides?.get(key) ?? objectBox(object),
            z: object.z,
        };
    };
}

/**
 * Ломаная стрелки: концы с привязкой берутся с узлов (центр — по контуру к другому концу),
 * свободные — из сохранённых точек; дальше — маршрут straight/orthogonal.
 */
export function connectorPoints(
    object: Pick<ShapeObject, "points" | "startRef" | "endRef" | "style">,
    lookup: (key: string) => AnchorNode | undefined,
): Point[] {
    const stored = object.points && object.points.length >= 2 ? object.points : [[0, 0], [0, 0]] as Point[];
    const startNode = object.startRef ? lookup(object.startRef.key) : undefined;
    const endNode = object.endRef ? lookup(object.endRef.key) : undefined;
    const freeStart = stored[0];
    const freeEnd = stored[stored.length - 1];
    const roughStart = startNode && object.startRef ? resolveAnchorPoint(startNode, object.startRef.anchor, null) : freeStart;
    const roughEnd = endNode && object.endRef ? resolveAnchorPoint(endNode, object.endRef.anchor, null) : freeEnd;
    const start = startNode && object.startRef ? resolveAnchorPoint(startNode, object.startRef.anchor, roughEnd) : freeStart;
    const end = endNode && object.endRef ? resolveAnchorPoint(endNode, object.endRef.anchor, roughStart) : freeEnd;
    if (stored.length > 2) return preservePolyline(stored, [round1(start[0]), round1(start[1])], [round1(end[0]), round1(end[1])], object.style.route);
    return routeConnector(
        [round1(start[0]), round1(start[1])],
        [round1(end[0]), round1(end[1])],
        object.style.route,
        startNode ? object.startRef?.anchor : null,
        endNode ? object.endRef?.anchor : null,
    );
}

function round1(value: number): number {
    return Math.round(value * 10) / 10;
}

export function withConnectorGeometry(object: ShapeObject, points: Point[]): ShapeObject {
    const box = boundsOfPoints(points);
    return { ...object, points, x: box.x, y: box.y, w: box.w, h: box.h };
}

function samePoints(a: Point[] | null, b: Point[]): boolean {
    if (!a || a.length !== b.length) return false;
    return a.every((point, index) => point[0] === b[index][0] && point[1] === b[index][1]);
}

/**
 * Пересчитывает стрелки после изменения узлов. changed — ключи изменённых узлов
 * (без него — все стрелки). Ссылки на исчезнувшие узлы обнуляются: стрелка
 * «отцепляется», оставаясь на месте. Возвращает только изменившиеся стрелки.
 */
export function syncConnectors(scene: Scene, changed?: Set<string>, overrides?: Map<string, Box>): Map<string, ShapeObject> {
    const lookup = nodeLookup(scene, overrides);
    const updates = new Map<string, ShapeObject>();
    for (const object of scene.values()) {
        if (object.type !== "shape" || !isConnectorKind(object.kind)) continue;
        const touches = !changed
            || (object.startRef && changed.has(object.startRef.key))
            || (object.endRef && changed.has(object.endRef.key));
        if (!touches) continue;
        let next = object;
        if (next.startRef && !lookup(next.startRef.key)) next = { ...next, startRef: null };
        if (next.endRef && !lookup(next.endRef.key)) next = { ...next, endRef: null };
        const points = connectorPoints(next, lookup);
        if (next !== object || !samePoints(object.points, points)) updates.set(object.key, withConnectorGeometry(next, points));
    }
    return updates;
}

/** Ключи стрелок, которые держатся за любой из узлов keys. */
export function connectorsAttachedTo(scene: Scene, keys: Set<string>): string[] {
    const result: string[] = [];
    for (const object of scene.values()) {
        if (object.type !== "shape" || !isConnectorKind(object.kind)) continue;
        if ((object.startRef && keys.has(object.startRef.key)) || (object.endRef && keys.has(object.endRef.key))) result.push(object.key);
    }
    return result;
}

/** Фигуры, которые при перетаскивании везут своё содержимое. */
const CONTAINER_KINDS: ReadonlySet<ShapeKind> = new Set<ShapeKind>(["rect", "ellipse", "diamond"]);

export function isContainer(object: SceneObject): object is ShapeObject {
    return object.type === "shape" && CONTAINER_KINDS.has(object.kind);
}

function boxInside(inner: Box, outer: Box): boolean {
    return inner.x >= outer.x && inner.y >= outer.y && inner.x + inner.w <= outer.x + outer.w && inner.y + inner.h <= outer.y + outer.h;
}

/**
 * Что поедет вместе с перетаскиваемыми фигурами-контейнерами: всё, что целиком лежит внутри
 * (тексты, фигуры, стикеры, свободные стрелки), с вложенностью до конца. Сами keys в ответ не входят.
 */
export function containedKeys(scene: Scene, keys: Iterable<string>): string[] {
    const seen = new Set(keys);
    const queue = [...seen];
    const result: string[] = [];
    while (queue.length > 0) {
        const container = scene.get(queue.pop()!);
        if (!container || !isContainer(container)) continue;
        const outer = objectBox(container);
        for (const object of scene.values()) {
            if (seen.has(object.key) || !boxInside(objectBox(object), outer)) continue;
            seen.add(object.key);
            result.push(object.key);
            queue.push(object.key);
        }
    }
    return result;
}

export function maxZ(scene: Scene): number {
    let result = 0;
    for (const object of scene.values()) result = Math.max(result, object.z);
    return result;
}

export function minZ(scene: Scene): number {
    let result = 0;
    for (const object of scene.values()) result = Math.min(result, object.z);
    return result;
}

/** «На передний план» / «на задний»: выделенные уходят выше/ниже всех, сохраняя свой порядок. */
export function reorderObjects(scene: Scene, keys: string[], direction: "front" | "back"): Map<string, SceneObject> {
    const selected = keys
        .map((key) => scene.get(key))
        .filter((object): object is SceneObject => Boolean(object))
        .sort((a, b) => a.z - b.z || a.key.localeCompare(b.key));
    const updates = new Map<string, SceneObject>();
    if (selected.length === 0) return updates;
    const selectedKeys = new Set(selected.map((object) => object.key));
    const rest = [...scene.values()].filter((object) => !selectedKeys.has(object.key));
    if (direction === "front") {
        const base = rest.reduce((top, object) => Math.max(top, object.z), -Infinity);
        const start = Number.isFinite(base) ? base + 1 : 0;
        selected.forEach((object, index) => updates.set(object.key, { ...object, z: start + index }));
    } else {
        const base = rest.reduce((bottom, object) => Math.min(bottom, object.z), Infinity);
        const start = (Number.isFinite(base) ? base : 0) - selected.length;
        selected.forEach((object, index) => updates.set(object.key, { ...object, z: start + index }));
    }
    return updates;
}

/** Порядок отрисовки: слой, затем ключ — одинаковый у всех клиентов. */
export function sortedObjects(scene: Scene): SceneObject[] {
    return [...scene.values()].sort((a, b) => a.z - b.z || a.key.localeCompare(b.key, "en", { numeric: true }));
}

/**
 * Данные фигуры для сервера; "defer" — в ней ссылка на ещё не созданную фигуру.
 * create — с видом фигуры; в обновлении вида нет (схема бэкенда его не принимает).
 */
export function shapeToData(object: ShapeObject, registry: IdRegistry, createdInBatch: Set<string>, mode: "create" | "update" = "create"): ShapeData | "defer" {
    const common = { style: object.style, label: object.label, z_index: object.z };
    const kind = mode === "create" ? { kind: object.kind } : {};
    if (!isConnectorKind(object.kind)) {
        return {
            ...kind,
            x: clampCoordinate(object.x),
            y: clampCoordinate(object.y),
            w: Math.min(SHAPE_LIMITS.maxW, Math.max(1, round1(object.w))),
            h: Math.min(SHAPE_LIMITS.maxH, Math.max(1, round1(object.h))),
            ...common,
        };
    }
    const startRef = object.startRef ? refToServer(object.startRef, registry, createdInBatch) : null;
    const endRef = object.endRef ? refToServer(object.endRef, registry, createdInBatch) : null;
    if (startRef === "defer" || endRef === "defer") return "defer";
    const points = (object.points ?? []).map(([x, y]) => [clampCoordinate(x), clampCoordinate(y)] as Point);
    return {
        ...kind,
        points: points.length >= 2 ? points : [[clampCoordinate(object.x), clampCoordinate(object.y)], [clampCoordinate(object.x + object.w), clampCoordinate(object.y + object.h)]],
        start_ref: startRef,
        end_ref: endRef,
        ...common,
    };
}

/**
 * Копии объектов для вставки: фигуры получают новые ключи и сдвиг, ссылки
 * между копируемыми фигурами переводятся на копии, на прочие узлы — отпускаются
 * (как в Figma: копия стрелки не цепляется к оригиналу). Стикеры возвращаются
 * отдельно — их копии создаёт сервер.
 */
export function duplicateShapes(
    objects: SceneObject[],
    offset: number,
    newKey: () => string,
    zStart: number,
): { shapes: ShapeObject[]; stickers: StickerObject[]; keyMap: Map<string, string> } {
    const ordered = [...objects].sort((a, b) => a.z - b.z);
    const keyMap = new Map(ordered.map((object) => {
        const generated = newKey();
        return [object.key, object.type === "sticker" ? "s" + generated : generated];
    }));
    const remap = (ref: LocalRef | null): LocalRef | null => {
        if (!ref) return null;
        const key = keyMap.get(ref.key);
        return key ? { ...ref, key } : null;
    };
    const copies = ordered.map((object, index): SceneObject => {
        const base = { ...object, key: keyMap.get(object.key)!, x: object.x + offset, y: object.y + offset, z: zStart + index };
        if (base.type === "sticker") return { ...base, id: 0, sticker: { ...base.sticker, id: 0 } };
        return {
            ...base,
            points: base.points?.map(([x, y]) => [x + offset, y + offset] as Point) ?? null,
            startRef: remap(base.startRef), endRef: remap(base.endRef),
        };
    });
    return {
        keyMap,
        shapes: copies.filter((item): item is ShapeObject => item.type === "shape"),
        stickers: copies.filter((item): item is StickerObject => item.type === "sticker"),
    };
}

/** Применяет снимки (из истории) к сцене: null — удалить, объект — поставить. */
export function applySnapshots(scene: Scene, snapshots: Map<string, SceneObject | null>): { scene: Scene; changed: Set<string> } {
    const next: Scene = new Map(scene);
    const changed = new Set<string>();
    for (const [key, snapshot] of snapshots) {
        const current = next.get(key);
        if (snapshot === null) {
            if (current) { next.delete(key); changed.add(key); }
            continue;
        }
        next.set(key, snapshot);
        changed.add(key);
    }
    return { scene: next, changed };
}
