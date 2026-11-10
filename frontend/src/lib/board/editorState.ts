import type { Scene, SceneObject, IdRegistry, LocalRef } from "@/lib/board/scene";
import { stickerObject, withConnectorGeometry } from "@/lib/board/scene";
import { encodeTemplate } from "@/lib/board/metadata";
import type { BoardGrouping } from "@/lib/board/group";
import { lockedSelection } from "@/lib/board/group";
import type { BoardTemplate, TemplateInsertion } from "@/lib/board/template";
import type { BoardSticker } from "@/lib/board/stickers";
import { defaultStyle, isConnectorKind, type BatchEditorState } from "@/lib/board/types";

export interface LocalEditorValue extends BoardGrouping {
    type: "editor";
    templates: BoardTemplate[];
}
export type BoardHistoryValue = SceneObject | LocalEditorValue;
export const EDITOR_HISTORY_KEY = "@editor";
export const emptyEditor = (): LocalEditorValue => ({type: "editor", groups: [], locked: [], templates: []});

export function editorBatchState(
    scene: Scene, registry: IdRegistry, editor: LocalEditorValue,
    revision: number, creates: Set<string>,
): BatchEditorState {
    const identify = (key: string) => {
        const object = scene.get(key);
        if (!object) throw new Error("Объект группы уже удалён.");
        const id = registry.idOf(key);
        if (id !== undefined) return {type: object.type, id};
        if (creates.has(key)) return {type: object.type, client_id: key};
        throw new Error("Дождитесь сохранения объектов группы.");
    };
    return {
        expected_revision: revision,
        metadata: {
            version: 1,
            groups: editor.groups.map(group => ({...group, members: group.members.map(identify)})),
            locked: editor.locked.map(identify), templates: editor.templates.map(encodeTemplate),
        },
    };
}

/** Проверяется до commit: прямые и косвенные изменения locked объектов не применяются частично. */
export function assertEditableChanges(scene: Scene, candidate: Scene, editor: BoardGrouping, keys: Iterable<string>): void {
    for (const key of keys) {
        const current = scene.get(key);
        const next = candidate.get(key);
        if (current && lockedSelection([key], editor) && JSON.stringify(current) !== JSON.stringify(next)) {
            throw new Error("Сначала разблокируйте объекты или связанные с ними стрелки.");
        }
    }
}

export function insertionObjects(
    insertion: TemplateInsertion, registry: IdRegistry, projectId: number | null, boardId: number,
): { objects: Map<string, SceneObject>; grouping: BoardGrouping } {
    const keyMap = new Map(insertion.operations.map(op => [op.client_id, op.op === "create_sticker" ? registry.newStickerKey() : registry.newKey()]));
    const ref = (value: {type: "shape" | "sticker"; client_id?: string; anchor: LocalRef["anchor"]} | null | undefined): LocalRef | null =>
        value ? {type: value.type, key: keyMap.get(value.client_id!)!, anchor: value.anchor} : null;
    const objects = new Map<string, SceneObject>();
    for (const op of insertion.operations) {
        const key = keyMap.get(op.client_id)!;
        if (op.op === "create_sticker") {
            const sticker: BoardSticker = {
                ...op.data, id: 0, project_id: projectId ?? 0, board_id: boardId, revision: 1,
                created_by_user_id: null, created_by_username_snapshot: "", created_by_display_name_snapshot: "",
                created_at: "", updated_at: "",
            };
            objects.set(key, stickerObject(sticker, key));
        } else {
            const data = op.data;
            if (!data.kind) throw new Error("В шаблоне отсутствует вид фигуры.");
            const object = {
                type: "shape" as const, key, kind: data.kind,
                x: data.x ?? 0, y: data.y ?? 0, w: data.w ?? 0, h: data.h ?? 0,
                z: data.z_index, points: data.points ?? null,
                startRef: ref(data.start_ref), endRef: ref(data.end_ref),
                style: defaultStyle(data.kind, data.style), label: data.label,
            };
            objects.set(key, isConnectorKind(data.kind) ? withConnectorGeometry(object, data.points!) : object);
        }
    }
    return {
        objects,
        grouping: {
            groups: insertion.groups.map(group => ({...group, members: group.members.map(key => keyMap.get(key)!)})),
            locked: insertion.locked.map(key => keyMap.get(key)!),
        },
    };
}

/** JSON API дополняет необязательные поля; сравниваем смысл снимка, а не их присутствие. */
export function editorSignature(editor: LocalEditorValue): string {
    return JSON.stringify({
        groups: editor.groups, locked: [...editor.locked].sort(),
        templates: editor.templates.map(template => ({
            ...template,
            items: template.items.map(item => {
                if (item.type === "sticker") return {type: item.type, client_id: item.client_id, data: {
                    body: item.data.body, color: item.data.color, task_ids: [],
                    canvas_x: item.data.canvas_x, canvas_y: item.data.canvas_y,
                    width: item.data.width ?? 230, height: item.data.height ?? 230, z_index: item.data.z_index ?? 0,
                }};
                const data = item.data;
                const common = {kind: data.kind, style: defaultStyle(data.kind, data.style), label: data.label ?? "", z_index: data.z_index ?? 0};
                return {type: item.type, client_id: item.client_id, data: isConnectorKind(data.kind)
                    ? {...common, points: data.points, start_ref: data.start_ref ?? null, end_ref: data.end_ref ?? null}
                    : {...common, x: data.x, y: data.y, w: data.w, h: data.h}};
            }),
        })),
    });
}
