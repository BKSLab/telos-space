interface BoardEditorMetadata {
    version: 1;
    groups: {id: string; name: string; members: {type: "shape" | "sticker"; id: number}[]}[];
    locked: {type: "shape" | "sticker"; id: number}[];
    templates: unknown[];
}
export interface MockBoard {
    editor_metadata?: BoardEditorMetadata;
    id: number; project_id: number | null; title: string; position: number; revision: number;
    created_by_user_id: number | null; created_at: string; updated_at: string;
}

type Json = Record<string, unknown>;

import type { Page } from "@playwright/test";
import type { Project } from "../../src/lib/types";
export const stamp = "2026-09-20T10:00:00Z";
export function mockState() {
    const me = {id: 1, username: "tester", first_name: "Тест", last_name: "Тестов", middle_name: null, has_avatar: false};
    const people = [me, {...me, id:2, username:"anna", first_name:"Анна", last_name:"Примерова"}, {...me, id:3, username:"ivan", first_name:"Иван", last_name:"Демов"}];
    const projects: Project[] = [
        {id:1,key:"DEMO",name:"Учебный проект",owner_id:1,description_md:null,status:"ACTIVE",color:"#58a6ff",icon:null,start_date:null,due_date:null,order_index:0,created_at:stamp,updated_at:stamp},
        {id:2,key:"SAMPLE",name:"Личный план",owner_id:1,description_md:null,status:"PLANNING",color:"#3fb950",icon:null,start_date:null,due_date:null,order_index:1,created_at:stamp,updated_at:stamp},
        {id:3,key:"DRAFT",name:"Черновик",owner_id:1,description_md:null,status:"PLANNING",color:"#a371f7",icon:null,start_date:null,due_date:null,order_index:2,created_at:stamp,updated_at:stamp},
    ];
    const stages = [{id:1,project_id:1,name:"В работе",color:"#58a6ff",order_index:0,is_done_stage:false}];
    const tasks = Array.from({length:12}, (_, index)=>({id:100+index,project_id:1,stage_id:1,number:index+1,key:`DEMO-${index+1}`,title:index===0?"Согласовать макеты личного кабинета":`Задача ${index+1}`,description_md:null,priority:"MEDIUM",role:null,assignee:null,start_date:null,due_date:null,baseline_start_date:null,baseline_due_date:null,completed_at:null,position:index*1000,created_at:stamp,updated_at:stamp,comments_count:0,last_comment:null,checklist:null,checklist_revision:0,participants:[]}));
    const members = people.map((user,index)=>({id:index+1,project_id:1,role:index===0?"OWNER":"MEMBER",user,created_at:stamp}));
    const boards: MockBoard[] = [
        { id: 1, project_id: 1, title: "Основная", position: 0, revision: 1, created_by_user_id: 1, created_at: stamp, updated_at: stamp },
        { id: 2, project_id: 1, title: "Архитектура", position: 1, revision: 1, created_by_user_id: 1, created_at: stamp, updated_at: stamp },
    ];
    const stickerBase = { project_id: 1, created_at: stamp, updated_at: stamp, revision: 1, task_ids: [] as number[], width: 230, height: 230 };
    const stickers: Json[] = [
        { ...stickerBase, id: 1, board_id: 1, z_index: 1, body: "Решили: пилот — на 10 клиентах из Москвы", color: "#fff19a", canvas_x: 40, canvas_y: 40, created_by_user_id: 1, created_by_username_snapshot: "tester", created_by_display_name_snapshot: "Тестов Тест", task_ids: [100] },
        { ...stickerBase, id: 2, board_id: 1, z_index: 2, body: "Вопрос: кто принимает макеты со стороны продаж?", color: "#c9edff", canvas_x: 320, canvas_y: 80, created_by_user_id: 2, created_by_username_snapshot: "anna", created_by_display_name_snapshot: "Примерова Анна" },
        { ...stickerBase, id: 3, board_id: 1, z_index: 3, body: "Идея: показывать статус доставки на карте", color: "#d8f5b5", canvas_x: 600, canvas_y: 30, created_by_user_id: 3, created_by_username_snapshot: "ivan", created_by_display_name_snapshot: "Демов Иван" },
    ];
    const shapes: Json[] = [];

    return {me,projects,stages,tasks,members,boards,stickers,shapes,tim:timState(),nextIds:{board:10,sticker:10,shape:100},writes:[] as {method:string;path:string;body:unknown}[],errors:[] as string[]};
}
export type MockState = ReturnType<typeof mockState>;
export async function installMockApi(page: Page, state: MockState) {
    page.on("pageerror", error=>state.errors.push(error.message));
    await page.route("**/api/v1/**", async route=>{
        const request=route.request(); const url=new URL(request.url()); const path=url.pathname.replace("/api/v1", ""); const method=request.method();
        const headers={"access-control-allow-origin":request.headers()["origin"]??"http://127.0.0.1:4179","access-control-allow-credentials":"true","access-control-allow-headers":"content-type","access-control-allow-methods":"GET, POST, PATCH, DELETE, PUT, OPTIONS"};
        const reply=(json:unknown,status=200)=>route.fulfill({status,headers,...(status===204?{}:{json})});
        if(method==="OPTIONS")return reply(null,204);
        const body=()=>{try{return request.postDataJSON();}catch{return {};}};
        if(method!=="GET")state.writes.push({method,path,body:body()});
        if(path==="/auth/me")return reply(state.me);
        if(path.startsWith("/tim/conversations")){const result=handleTim(state,path,method,body);return reply(result.json,result.status??200);}
        const taskMatch=/^\/tasks\/(\d+)(\/.*)?$/.exec(path);
        if(taskMatch){
            const id=Number(taskMatch[1]);const rest=taskMatch[2]??"";
            if(rest==="")return reply(state.tasks.find(task=>task.id===id));
            if(rest==="/attention")return reply({task_id:id,revision:1,deadline_revision:1,overdue:false,is_new:false,is_overdue:false,is_updated:false});
            if(rest==="/view")return reply(null,204);
            if(rest==="/report")return reply({task_id:id,revision:0,body_md:null,author:null,published_at:null});
            if(rest==="/risks")return reply({items:[],total:0,offset:0,limit:50});
            return reply([]);
        }
        if(path==="/my/tasks")return reply([]);
        if(path==="/my/task-attention")return reply({new_tasks:0,overdue_tasks:0,updated_tasks:0,total:0,items:[]});
        if(path==="/dashboard/analytics")return reply(null);
        if(path==="/boards"||path.startsWith("/boards/")){const result=handleBoards(state,null,path,method,url,body);return reply(result.json,result.status??200);}
        if(path==="/projects/defaults")return reply({stages:state.stages});
        if(path==="/projects")return reply(state.projects);
        const projectMatch=/^\/projects\/(\d+)(\/.*)?$/.exec(path);
        if(projectMatch){
            const id=Number(projectMatch[1]);const rest=projectMatch[2]??"";
            if(rest==="/boards"||rest.startsWith("/boards/")){const result=handleBoards(state,id,rest,method,url,body);return reply(result.json,result.status??200);}
            if(rest==="")return reply(state.projects.find(project=>project.id===id));
            if(rest.startsWith("/agent/conversations")){const result=handleTim(state,rest.replace("/agent", "/tim"),method,body);return reply(result.json,result.status??200);}
            if(rest==="/members")return reply(state.members);
            if(rest==="/tasks")return reply(state.tasks);
            if(rest==="/stages")return reply(state.stages);
            if(rest==="/wbs")return reply({nodes:[],tasks:state.tasks,stats:{total_nodes:0,total_tasks:state.tasks.length,assigned_tasks:0,unassigned_tasks:state.tasks.length,done_tasks:0,overdue_tasks:0}});
            if(rest==="/risks")return reply({items:[],total:0,page:1,page_size:5});
            if(rest==="/risks/summary")return reply({active_risks:0,high_risks:0,risks_due_for_review:0,signals:[]});
            if(rest==="/stats")return reply({project_id:id,total_tasks:12,done_tasks:0,in_progress_tasks:12,overdue_tasks:0,due_soon_tasks:0,unassigned_tasks:0,completion_rate:0,next_due_date:null,stage_breakdown:[]});
        }
        if(path==="/dashboard")return reply({totals:{total_projects:3,active_projects:3,total_tasks:12,done_tasks:0,in_progress_tasks:12,overdue_tasks:0,completion_rate:0},recent_tasks:[],attention_tasks:[],projects:[],tasks:[],kpis:{active_projects:3,total_tasks:12,overdue_tasks:0,due_soon_tasks:0,completion_rate:0},project_stats:[],action_items:[],recent_activity:[]});
        if(path==="/project-chats/unread")return reply([]);
        return reply([]);
    });
}
const DEFAULT_STYLE = { stroke: "neutral", fill: "none", stroke_width: 2, dash: "solid", route: "straight", start_head: "none", end_head: "none", font_size: "m", text_align: "center" };

/** Доски, стикеры и фигуры по контракту docs/boards-contract.md — с состоянием между запросами. */
export function handleBoards(state: MockState, projectId: number | null, rest: string, method: string, url: URL, body: () => unknown): { json: unknown; status?: number } {
    const read = () => { try { return (body() ?? {}) as Json; } catch { return {} as Json; } };
    const normalizeTaskIds = (data: Json): Json => Array.isArray(data.task_ids)
        ? { ...data, task_ids: [...new Set(data.task_ids as number[])].sort((a, b) => a - b) }
        : data;
    const active = () => state.boards.filter((board) => board.project_id === projectId).sort((a, b) => a.position - b.position || a.id - b.id);
    if (rest === "/boards") {
        if (method === "POST") {
            const board: MockBoard = { id: state.nextIds.board++, project_id: projectId, title: String(read().title ?? "Доска"), position: Math.max(0, ...active().map((item) => item.position)) + 1, revision: 1, created_by_user_id: 1, created_at: stamp, updated_at: stamp };
            state.boards.push(board);
            return { json: board, status: 201 };
        }
        return { json: active() };
    }
    const match = /^\/boards\/(\d+)(\/.*)?$/.exec(rest);
    if (!match) return { json: { detail: "Не найдено" }, status: 404 };
    const boardId = Number(match[1]);
    const board = state.boards.find((item) => item.id === boardId && item.project_id === projectId);
    if (!board) return { json: { detail: "Доска не найдена" }, status: 404 };
    const sub = match[2] ?? "";
    const boardStickers = () => state.stickers.filter((sticker) => sticker.board_id === boardId);
    const boardShapes = () => state.shapes.filter((shape) => shape.board_id === boardId);
    const detachFrom = (type: string, id: number) => {
        for (const shape of boardShapes()) {
            for (const end of ["start_ref", "end_ref"]) {
                const ref = shape[end] as Json | null;
                if (ref && ref.type === type && ref.id === id) { shape[end] = null; shape.revision = Number(shape.revision) + 1; }
            }
        }
    };
    const withBounds = (shape: Json): Json => {
        const points = shape.points as number[][] | null;
        if (!points || points.length < 2) return shape;
        const xs = points.map((point) => point[0]);
        const ys = points.map((point) => point[1]);
        return Object.assign(shape, { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) });
    };
    const makeShape = (data: Json): Json => withBounds({
        id: state.nextIds.shape++, board_id: boardId, points: null, start_ref: null, end_ref: null, label: "", z_index: 0,
        created_by_user_id: 1, revision: 1, created_at: stamp, updated_at: stamp, ...data,
        style: { ...DEFAULT_STYLE, ...(data.kind === "arrow" ? { end_head: "arrow" } : {}), ...((data.style as Json | undefined) ?? {}) },
    });
    if (sub === "") {
        if (method === "PATCH") {
            const request = read();
            if (request.revision !== board.revision) return { json: { detail: "Доску уже изменили" }, status: 409 };
            if (typeof request.title === "string") board.title = request.title;
            board.revision += 1;
            return { json: board };
        }
        if (method === "DELETE") {
            if (projectId !== null && active().length <= 1) return { json: { detail: "Нельзя удалить последнюю доску проекта." }, status: 409 };
            state.boards = state.boards.filter((item) => item.id !== boardId);
            return { json: null, status: 204 };
        }
        return { json: board };
    }
    const emptyMetadata = (): BoardEditorMetadata => ({version: 1, groups: [], locked: [], templates: []});
    if (sub === "/editor-metadata") {
        if (method === "PUT") {
            const data = read();
            if (data.expected_revision !== board.revision) return {json: {detail: "Доску уже изменили"}, status: 409};
            board.editor_metadata = data.metadata as BoardEditorMetadata;
            board.revision += 1;
        }
        return {json: {board_id: board.id, revision: board.revision, metadata: board.editor_metadata ?? emptyMetadata()}};
    }
    if (sub === "/create-project" && method === "POST") {
        const data = read();
        if (projectId !== null || data.expected_revision !== board.revision) return {json: {detail: "Доску уже изменили"}, status: 409};
        const requested = data.project as Json;
        const id = Math.max(...state.projects.map(project => project.id)) + 1;
        const project = {...state.projects[0], ...requested, id};
        state.projects.push(project);
        board.project_id = id;
        board.revision += 1;
        for (const sticker of boardStickers()) { sticker.project_id = id; sticker.revision = Number(sticker.revision) + 1; }
        return {json: {board, project}, status: 201};
    }
    if (sub === "/content") return { json: { board, stickers: boardStickers(), shapes: boardShapes() } };
    if (sub === "/stickers") {
        if (method === "POST") {
            const request = read();
            const sticker = {
                id: state.nextIds.sticker++, project_id: projectId, board_id: boardId, z_index: 0, width: 230, height: 230, task_ids: [],
                created_by_user_id: 1, created_by_username_snapshot: "tester", created_by_display_name_snapshot: "Тестов Тест",
                revision: 1, created_at: stamp, updated_at: stamp, ...normalizeTaskIds(request),
            };
            state.stickers.push(sticker);
            return { json: sticker, status: 201 };
        }
        return { json: boardStickers() };
    }
    const stickerMatch = /^\/stickers\/(\d+)(\/position)?$/.exec(sub);
    if (stickerMatch) {
        const sticker = boardStickers().find((item) => item.id === Number(stickerMatch[1]));
        if (!sticker) return { json: { detail: "Стикер не найден" }, status: 404 };
        if (method === "DELETE") {
            state.stickers = state.stickers.filter((item) => item !== sticker);
            detachFrom("sticker", Number(sticker.id));
            return { json: null, status: 204 };
        }
        const request = read();
        delete request.revision;
        Object.assign(sticker, normalizeTaskIds(request), { revision: Number(sticker.revision) + 1, updated_at: new Date().toISOString() });
        return { json: sticker };
    }
    if (sub === "/batch" && method === "POST") {
        const ops = read().ops;
        if (!Array.isArray(ops) || (!read().editor_state && ops.length < 1) || ops.length > 500) {
            return { json: { detail: "В пакете должно быть от 1 до 500 операций" }, status: 422 };
        }
        const editorState = read().editor_state as {expected_revision: number; metadata: {version: 1; groups: {id:string;name:string;members:{type:"shape"|"sticker";id?:number;client_id?:string}[]}[]; locked:{type:"shape"|"sticker";id?:number;client_id?:string}[]; templates:BoardEditorMetadata["templates"]}} | undefined;
        if (editorState && editorState.expected_revision !== board.revision) return {json: {detail: "Доску уже изменили"}, status: 409};
        const clientIds = new Set<string>();
        for (const op of ops as Json[]) {
            if (!op || !["create_shape", "update_shape", "delete_shape", "create_sticker", "update_sticker", "delete_sticker", "move_sticker"].includes(String(op.op))) {
                return { json: { detail: "Недопустимая операция" }, status: 422 };
            }
            if (op.op !== "create_shape" && op.op !== "create_sticker") continue;
            if (op.client_id == null && op.op === "create_shape") continue;
            if (typeof op.client_id !== "string" || op.client_id.length < 1 || op.client_id.length > 64 || clientIds.has(op.client_id)) {
                return { json: { detail: "client_id в пакете должны быть уникальны" }, status: 422 };
            }
            clientIds.add(op.client_id);
        }
        // Выполняем на копии: ни записи, ни отцепленные refs, ни ревизии не утекут при поздней ошибке.
        const original = state;
        state = { ...state, stickers: structuredClone(state.stickers), shapes: structuredClone(state.shapes), nextIds: { ...state.nextIds } };
        const clientTargets = new Map<string, { type: string; id: number }>();
        // Отцепление refs повышает фактическую ревизию, но не создаёт конфликт внутри пакета.
        const expectedShapeRevisions = new Map(boardShapes().map(shape => [Number(shape.id), Number(shape.revision)]));
        const changed: Json[] = [];
        const deleted: number[] = [];
        const movedStickers: Json[] = [];
        const deletedStickers: number[] = [];
        const resolveRef = (value: unknown, selfId?: number): Json | null | undefined => {
            if (value === null) return null;
            if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
            const ref = value as Json;
            if (ref.type !== "shape" && ref.type !== "sticker") return undefined;
            if (Object.keys(ref).some(key => !["type", "id", "client_id", "anchor"].includes(key))) return undefined;
            const anchor = ref.anchor === undefined ? "center" : ref.anchor;
            if (typeof anchor !== "string" || !["top", "right", "bottom", "left", "center"].includes(anchor)) return undefined;
            const hasId = ref.id != null;
            const hasClientId = ref.client_id != null;
            if (hasId === hasClientId) return undefined;
            let id = ref.id;
            if (hasClientId) {
                if (typeof ref.client_id !== "string") return undefined;
                const target = clientTargets.get(ref.client_id);
                if (!target || target.type !== ref.type) return undefined;
                id = target.id;
            }
            if (typeof id !== "number" || !Number.isInteger(id) || id <= 0) return undefined;
            const target = (ref.type === "shape" ? boardShapes() : boardStickers()).find(item => item.id === id);
            if (!target || (ref.type === "shape" && (id === selfId || target.kind === "arrow" || target.kind === "line"))) return undefined;
            return { type: ref.type, id, anchor };
        };
        // Те же ограничения, что в схемах бэкенда (backend/src/schemas/board_shapes.py, extra=forbid).
        const linear = (kind: unknown) => kind === "arrow" || kind === "line";
        for (const op of ops) {
            const data = (op.data as Json | undefined) ?? {};
            const target = op.op === "update_shape" || op.op === "delete_shape" ? boardShapes().find((item) => item.id === op.id) : null;
            if (op.op === "update_shape" || op.op === "delete_shape") {
                if (!target) return { json: { detail: "Фигура не найдена" }, status: 404 };
                if (op.revision != null && expectedShapeRevisions.get(Number(op.id)) !== op.revision) return { json: { detail: "Конфликт фигуры" }, status: 409 };
            }
            const kind = op.op === "create_shape" ? data.kind : target?.kind;
            const invalid = (op.op === "update_shape" && "kind" in data)
                || ((op.op === "create_shape" || op.op === "update_shape") && Object.entries(data).some(([key, value]) => value === null && key !== "start_ref" && key !== "end_ref"))
                || ((op.op === "create_shape" || op.op === "update_shape") && linear(kind) && ((op.op === "create_shape" || "points" in data) && !Array.isArray(data.points) || "w" in data || "h" in data))
                || ((op.op === "create_shape" || op.op === "update_shape") && !linear(kind) && ("points" in data || "start_ref" in data || "end_ref" in data || Number(data.w) < 1 || Number(data.h) < 1));
            if (invalid) {
                state.errors.push(`422 batch: ${JSON.stringify(op)}`);
                return { json: { detail: "Недопустимые данные фигуры" }, status: 422 };
            }
            const refs: Json = {};
            if (op.op === "create_shape" || op.op === "update_shape") {
                for (const name of ["start_ref", "end_ref"]) {
                    if (!(name in data)) continue;
                    const ref = resolveRef(data[name], target ? Number(target.id) : undefined);
                    if (ref === undefined) return { json: { detail: "Недопустимая привязка фигуры" }, status: 422 };
                    refs[name] = ref;
                }
            }
            if (op.op === "create_shape") {
                const shape = makeShape({ ...data, ...refs });
                state.shapes.push(shape);
                if (op.client_id != null) clientTargets.set(op.client_id, { type: "shape", id: Number(shape.id) });
                changed.push(shape);
            } else if (op.op === "update_shape") {
                const shape = boardShapes().find((item) => item.id === op.id);
                if (!shape) return { json: { detail: "Фигура не найдена" }, status: 404 };
                const style = data.style ? { ...DEFAULT_STYLE, ...(shape.style as Json), ...(data.style as Json) } : shape.style;
                Object.assign(shape, data, refs, { style, revision: Number(shape.revision) + 1 });
                withBounds(shape);
                expectedShapeRevisions.set(Number(shape.id), (expectedShapeRevisions.get(Number(shape.id)) ?? Number(shape.revision) - 1) + 1);
                changed.push(shape);
            } else if (op.op === "delete_shape") {
                state.shapes = state.shapes.filter((item) => item !== target);
                detachFrom("shape", Number(op.id));
                expectedShapeRevisions.delete(Number(op.id));
                deleted.push(Number(op.id));
            } else if (op.op === "create_sticker") {
                const sticker = {
                    id: state.nextIds.sticker++, project_id: projectId, board_id: boardId,
                    canvas_x: 40, canvas_y: 40, width: 230, height: 230, z_index: 0, task_ids: [],
                    created_by_user_id: 1, created_by_username_snapshot: "tester",
                    created_by_display_name_snapshot: "Тестов Тест",
                    revision: 1, created_at: stamp, updated_at: stamp, ...normalizeTaskIds(data),
                };
                state.stickers.push(sticker);
                clientTargets.set(op.client_id, { type: "sticker", id: Number(sticker.id) });
                movedStickers.push(sticker);
            } else if (op.op === "update_sticker") {
                const sticker = boardStickers().find(item => item.id === op.id);
                if (!sticker || sticker.revision !== op.revision) return {json: {detail: "Конфликт стикера"}, status: 409};
                Object.assign(sticker, normalizeTaskIds(data), {revision: Number(sticker.revision) + 1});
                movedStickers.push(sticker);
            } else if (op.op === "delete_sticker") {
                const sticker = boardStickers().find(item => item.id === op.id);
                if (!sticker || sticker.revision !== op.revision) return {json: {detail: "Конфликт стикера"}, status: 409};
                state.stickers = state.stickers.filter(item => item !== sticker);
                detachFrom("sticker", Number(op.id));
                deletedStickers.push(Number(op.id));
            } else if (op.op === "move_sticker") {
                const sticker = boardStickers().find((item) => item.id === op.id);
                if (!sticker) return { json: { detail: "Стикер не найден" }, status: 404 };
                Object.assign(sticker, data);
                movedStickers.push(sticker);
            }
        }
        let editorResult = null;
        if (editorState || deleted.length || deletedStickers.length) {
            try {
                const resolveMeta = (ref: {type:"shape"|"sticker";id?:number;client_id?:string}) => {
                    const created = ref.client_id ? clientTargets.get(ref.client_id) : undefined;
                    const id = ref.id ?? created?.id;
                    if (created && created.type !== ref.type) throw new Error("type");
                    const exists = (ref.type === "shape" ? boardShapes() : boardStickers()).some(item => item.id === id);
                    if (!exists || !Number.isSafeInteger(id)) throw new Error("ref");
                    return {type: ref.type, id: id!};
                };
                const raw = editorState?.metadata ?? board.editor_metadata ?? emptyMetadata();
                const removed = new Set([...deleted.map(id => "shape:" + id), ...deletedStickers.map(id => "sticker:" + id)]);
                const refs = (items: {type:"shape"|"sticker";id?:number;client_id?:string}[]) =>
                    items.filter(ref => editorState || !removed.has(ref.type + ":" + ref.id)).map(resolveMeta);
                const metadata: BoardEditorMetadata = {
                    version: 1, groups: raw.groups.map(group => ({...group, members: refs(group.members)})).filter(group => group.members.length >= 2),
                    locked: refs(raw.locked), templates: raw.templates,
                };
                const locked = new Set(metadata.locked.map(ref => ref.type + ":" + ref.id));
                for (const group of metadata.groups) {
                    if (group.members.some(ref => locked.has(ref.type + ":" + ref.id))) {
                        group.members.forEach(ref => locked.add(ref.type + ":" + ref.id));
                    }
                }
                for (const op of ops as Json[]) {
                    const type = String(op.op).includes("sticker") ? "sticker" : "shape";
                    if (!String(op.op).startsWith("create_") && locked.has(type + ":" + op.id)) throw new Error("locked");
                }
                board.editor_metadata = metadata;
                board.revision += 1;
                editorResult = {board_id: board.id, revision: board.revision, metadata};
            } catch { return {json: {detail: "Недопустимые настройки редактора"}, status: 422}; }
        }
        original.shapes = state.shapes;
        original.stickers = state.stickers;
        original.nextIds = state.nextIds;
        const idMap = Object.fromEntries([...clientTargets].map(([clientId, target]) => [clientId, target.id]));
        return { json: { shapes: changed, deleted_shape_ids: deleted, stickers: movedStickers, id_map: idMap, deleted_sticker_ids: deletedStickers, editor_state: editorResult } };
    }
    const toSticker = /^\/shapes\/(\d+)\/to-sticker$/.exec(sub);
    if (toSticker && method === "POST") {
        const shape = boardShapes().find((item) => item.id === Number(toSticker[1]));
        if (!shape) return { json: { detail: "Фигура не найдена" }, status: 404 };
        const clamp = (value: number) => Math.max(160, Math.min(520, value));
        const sticker = {
            id: state.nextIds.sticker++, project_id: projectId, board_id: boardId, body: String(shape.label || "Новый стикер"), color: "#fff19a",
            canvas_x: shape.x, canvas_y: shape.y, width: clamp(Number(shape.w)), height: clamp(Number(shape.h)), z_index: shape.z_index, task_ids: [],
            created_by_user_id: 1, created_by_username_snapshot: "tester", created_by_display_name_snapshot: "Тестов Тест",
            revision: 1, created_at: stamp, updated_at: stamp,
        };
        state.stickers.push(sticker);
        const updated: Json[] = [];
        for (const other of boardShapes()) {
            for (const end of ["start_ref", "end_ref"]) {
                const ref = other[end] as Json | null;
                if (ref && ref.type === "shape" && ref.id === shape.id) {
                    other[end] = { type: "sticker", id: sticker.id, anchor: ref.anchor };
                    other.revision = Number(other.revision) + 1;
                    if (!updated.includes(other)) updated.push(other);
                }
            }
        }
        state.shapes = state.shapes.filter((item) => item !== shape);
        return { json: { sticker, updated_shapes: updated } };
    }
    void url;
    return { json: { detail: "Не найдено" }, status: 404 };
}




type TimMessage = {
    id: number; conversation_id: number; request_id: string; role: "user" | "assistant"; content: string;
    sources: Json[]; actions?: Json[]; files?: Json[]; page_context: Json | null;
    status: "queued" | "processing" | "completed" | "failed"; error: string | null; created_at: string;
    progress?: string | null;
};

/** Состояние мока личного диалога с Арисом: диалоги, реплики и рычаги для тестов. */
function timState() {
    return {
        conversations: [] as { id: number; title: string; created_at: string; updated_at: string }[],
        messages: new Map<number, TimMessage[]>(),
        /** Ответ Ариса; pending — ответ ещё готовится, fail — попытка упадёт. */
        answer: "По задачам всё в порядке: просрочек нет.",
        sources: [] as Json[],
        action: null as Json | null,
        pending: false,
        /** Живой статус, пока ответ готовится (pending): «Читаю задачу…»; null — «думает». */
        progress: null as string | null,
        fail: false,
        submissions: [] as Json[],
        decisions: [] as Json[],
        nextId: 0,
    };
}

const TIM_FILE = { id: "f918d205-2ced-48eb-85f4-19b8e55ef361", original_name: "acceptance.md", content_type: "text/markdown", size: 12, created_at: stamp };

/** Эндпоинты /tim/conversations* описаны в docs/agent.md. */
function handleTim(state: MockState, path: string, method: string, body: () => unknown): { json: unknown; status?: number } {
    const tim = state.tim;
    if (path === "/tim/conversations") {
        if (method === "GET") return { json: { items: tim.conversations.slice().reverse(), next_offset: null } };
        const conversation = { id: tim.conversations.length + 1, title: "Новый диалог", created_at: stamp, updated_at: stamp };
        tim.conversations.push(conversation);
        tim.messages.set(conversation.id, []);
        return { json: conversation, status: 201 };
    }
    const files = /^\/tim\/conversations\/(\d+)\/files(\/[^/]+)?$/.exec(path);
    if (files) return files[2] ? { json: null, status: 204 } : { json: TIM_FILE, status: 201 };
    const decision = /^\/tim\/conversations\/(\d+)\/actions\/([^/]+)\/decision$/.exec(path);
    if (decision) {
        const data = body() as { decision: "approve" | "reject" };
        tim.decisions.push(data);
        const assistant = tim.messages.get(Number(decision[1]))!.at(-1)!;
        const action = assistant.actions!.find((item) => item.id === decision[2])!;
        action.status = data.decision === "approve" ? "completed" : "rejected";
        action.result = { message: action.status === "completed" ? "Задача удалена" : "Действие отклонено" };
        assistant.status = "queued";
        return { json: action };
    }
    const match = /^\/tim\/conversations\/(\d+)\/messages(?:\/(\d+)\/retry)?$/.exec(path);
    if (!match) return { json: { detail: "Не найдено" }, status: 404 };
    const id = Number(match[1]);
    const rows = tim.messages.get(id);
    if (!rows) return { json: { detail: "Диалог не найден" }, status: 404 };
    if (match[2]) {
        const message = rows.find((item) => item.id === Number(match[2]))!;
        Object.assign(message, { status: "queued", error: null });
        return { json: message, status: 202 };
    }
    if (method === "GET") {
        const assistant = rows.at(-1);
        if (assistant && (assistant.status === "queued" || assistant.status === "processing") && tim.pending && tim.progress) {
            Object.assign(assistant, { status: "processing", progress: tim.progress });
        }
        if (assistant && (assistant.status === "queued" || assistant.status === "processing") && !tim.pending) {
            Object.assign(assistant, tim.fail
                ? { status: "failed", error: "Не удалось подготовить ответ. Можно повторить запрос.", progress: null }
                : { status: "completed", content: tim.answer, sources: tim.sources, progress: null });
            if (tim.action && !assistant.actions) assistant.actions = [structuredClone(tim.action)];
        }
        return { json: { items: rows, next_before_id: null } };
    }
    const data = body() as { request_id: string; content: string; file_ids?: string[]; page_context?: Json | null };
    tim.submissions.push(data as unknown as Json);
    const common = { conversation_id: id, request_id: data.request_id, sources: [], error: null, created_at: stamp };
    const question: TimMessage = { ...common, id: ++tim.nextId, role: "user", content: data.content, status: "completed", page_context: data.page_context ?? null, files: data.file_ids?.length ? [TIM_FILE] : [] };
    const assistant: TimMessage = { ...common, id: ++tim.nextId, role: "assistant", content: "", status: "queued", page_context: null };
    rows.push(question, assistant);
    tim.conversations.find((item) => item.id === id)!.title = data.content;
    return { json: { user_message: question, assistant_message: assistant }, status: 202 };
}
