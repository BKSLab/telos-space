import { api, endpoints } from "@/lib/api";
import type { Project, ProjectCreate } from "@/lib/types";
import { IdRegistry, type Scene } from "@/lib/board/scene";
import type { BoardGrouping } from "@/lib/board/group";
import { validateTemplate, type BoardTemplate, type TemplateInsertion, type TemplateItem } from "@/lib/board/template";
import { defaultStyle, type Board, type ServerRef, type ShapeStyle } from "@/lib/board/types";

export interface BoardObjectId { type: "shape" | "sticker"; id: number }
type WireShapeData = Omit<Extract<TemplateItem, { type: "shape" }>["data"], "start_ref" | "end_ref" | "style"> & {
    style?: { [Key in keyof ShapeStyle]?: ShapeStyle[Key] | null } | null;
    start_ref?: { object_id: string; anchor: ServerRef["anchor"] } | null;
    end_ref?: { object_id: string; anchor: ServerRef["anchor"] } | null;
};
export interface StoredBoardTemplate {
    id: string;
    name: string;
    objects: (
        | { type: "shape"; object_id: string; data: WireShapeData }
        | { type: "sticker"; object_id: string; data: Extract<TemplateItem, { type: "sticker" }>["data"] }
    )[];
    groups?: BoardTemplate["groups"];
    locked?: string[];
}
export interface BoardEditorMetadata {
    version: 1;
    groups: { id: string; name: string; members: BoardObjectId[] }[];
    locked: BoardObjectId[];
    templates: StoredBoardTemplate[];
}
export interface BoardEditorState { board_id: number; revision: number; metadata: BoardEditorMetadata }

export function encodeTemplate(template: BoardTemplate): StoredBoardTemplate {
    validateTemplate(template);
    const ref = (value: ServerRef | null | undefined) => value
        ? { object_id: value.client_id!, anchor: value.anchor } : null;
    return {
        id: template.id, name: template.name,
        objects: template.items.map((item) => item.type === "sticker"
            ? { type: "sticker", object_id: item.client_id, data: { ...item.data, task_ids: [] } }
            : { type: "shape", object_id: item.client_id, data: { ...item.data,
                start_ref: ref(item.data.start_ref), end_ref: ref(item.data.end_ref) } }),
        ...(template.groups.length > 0 ? { groups: structuredClone(template.groups) } : {}),
        ...(template.locked.length > 0 ? { locked: [...template.locked] } : {}),
    };
}

export function decodeTemplate(template: StoredBoardTemplate): BoardTemplate {
    const types = new Map(template.objects.map((object) => [object.object_id, object.type]));
    const ref = (value: WireShapeData["start_ref"]): ServerRef | null => value
        ? { type: types.get(value.object_id)!, client_id: value.object_id, anchor: value.anchor } : null;
    const decoded: BoardTemplate = {
        version: 1, id: template.id, name: template.name,
        items: template.objects.map((item) => item.type === "sticker"
            ? { type: "sticker", client_id: item.object_id, data: structuredClone(item.data) }
            : { type: "shape", client_id: item.object_id, data: { ...structuredClone(item.data),
                style: defaultStyle(item.data.kind, Object.fromEntries(Object.entries(item.data.style ?? {})
                    .filter(([, value]) => value !== null)) as Partial<ShapeStyle>),
                start_ref: ref(item.data.start_ref), end_ref: ref(item.data.end_ref) } }),
        groups: structuredClone(template.groups ?? []), locked: [...(template.locked ?? [])],
    };
    validateTemplate(decoded);
    return decoded;
}

/** Call after the parent's scene save has completed, so n-keys have server IDs. */
export function encodeMetadata(
    scene: Scene, registry: IdRegistry, state: BoardGrouping, templates: BoardTemplate[],
): BoardEditorMetadata {
    const identify = (key: string): BoardObjectId => {
        const object = scene.get(key);
        const id = object?.type === "sticker" ? object.id : registry.idOf(key);
        if (!object || id === undefined || !Number.isSafeInteger(id) || id <= 0) {
            throw new Error("Сначала дождитесь сохранения объектов доски.");
        }
        return { type: object.type, id };
    };
    return {
        version: 1,
        groups: state.groups.map((group) => ({ ...group, members: group.members.map(identify) })),
        locked: state.locked.map(identify), templates: templates.map(encodeTemplate),
    };
}

export function decodeMetadata(metadata: BoardEditorMetadata, registry: IdRegistry): BoardGrouping & { templates: BoardTemplate[] } {
    const key = (ref: BoardObjectId) => ref.type === "sticker" ? registry.keyForSticker(ref.id) : registry.keyFor(ref.id);
    return {
        groups: metadata.groups.map((group) => ({ ...group, members: group.members.map(key) })),
        locked: metadata.locked.map(key), templates: metadata.templates.map(decodeTemplate),
    };
}

/** Insert objects, groups and locks in one version-checked board transaction. */
export function templateBatchRequest(insertion: TemplateInsertion, expectedRevision: number) {
    if (!Number.isSafeInteger(expectedRevision) || expectedRevision < 1) throw new Error("Обновите состояние доски.");
    return {
        ops: insertion.operations,
        editor_insert: { expected_revision: expectedRevision, groups: insertion.groups, locked: insertion.locked },
    };
}

export const editorMetadataEndpoint = (projectId: number | null, boardId: number) =>
    `${endpoints.board(projectId, boardId)}/editor-metadata`;
export const editorMetadataKey = (projectId: number | null, boardId: number) =>
    ["board-editor-metadata", projectId, boardId] as const;

export function loadEditorMetadata(projectId: number | null, boardId: number): Promise<BoardEditorState> {
    return api.get(editorMetadataEndpoint(projectId, boardId));
}
export function saveEditorMetadata(
    projectId: number | null, boardId: number, expectedRevision: number, metadata: BoardEditorMetadata,
): Promise<BoardEditorState> {
    return api.put(editorMetadataEndpoint(projectId, boardId), { expected_revision: expectedRevision, metadata });
}
export function createProjectFromBoard(
    boardId: number, expectedRevision: number, project: ProjectCreate,
): Promise<{ project: Project; board: Board }> {
    return api.post(`${endpoints.board(null, boardId)}/create-project`, { expected_revision: expectedRevision, project });
}
