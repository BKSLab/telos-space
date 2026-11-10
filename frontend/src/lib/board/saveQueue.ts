import { shapeToData, type IdRegistry, type Scene, type SceneObject } from "@/lib/board/scene";
import { clampCoordinate } from "@/lib/board/geometry";
import { isConnectorKind, type BatchOp, type BatchResponse, type BatchEditorState } from "@/lib/board/types";
import type { BoardStickerCreateInput } from "@/lib/board/stickers";

export const SAVE_DEBOUNCE_MS = 300;
export const MAX_BATCH_OPS = 500;

export interface SaveQueueOptions {
    getScene: () => Scene;
    registry: IdRegistry;
    send: (ops: BatchOp[], editorState?: BatchEditorState) => Promise<BatchResponse>;
    getEditorState?: (creates: Set<string>) => BatchEditorState;
    onFailure: (error: unknown) => void;
    onResponse?: (response: BatchResponse, snapshots: Map<string, SceneObject | undefined>) => void;
    onStateChange?: (state: { pending: boolean }) => void;
    debounceMs?: number;
}

export function stickerToData(object: Extract<SceneObject, { type: "sticker" }>): BoardStickerCreateInput {
    return {
        body: object.sticker.body, color: object.sticker.color, task_ids: object.sticker.task_ids,
        canvas_x: clampCoordinate(object.x), canvas_y: clampCoordinate(object.y),
        width: Math.round(object.w), height: Math.round(object.h), z_index: object.z,
    };
}

/** Последовательное сохранение смешанной сцены. Снимки истории сохраняют стабильные ключи даже после удаления и восстановления объектов. */
export class SaveQueue {
    private dirty = new Set<string>();
    private editorDirty = false;
    private timer: ReturnType<typeof setTimeout> | null = null;
    private inFlight: Promise<boolean> | null = null;
    private disposed = false;
    private persisted: Scene;
    private failure: unknown = null;
    private readonly options: SaveQueueOptions;

    constructor(options: SaveQueueOptions) {
        this.options = options;
        this.persisted = new Map(options.getScene());
    }

    get pending(): boolean { return this.dirty.size > 0 || this.editorDirty || this.inFlight !== null; }

    markDirty(keys: Iterable<string>): void {
        for (const key of keys) this.dirty.add(key);
        if (!this.dirty.size) return;
        this.notify(); this.schedule();
    }

    markEditorDirty(): void {
        this.editorDirty = true; this.notify(); this.schedule();
    }

    async flushNow(): Promise<void> {
        this.clearTimer();
        while (this.inFlight || this.dirty.size || this.editorDirty) {
            if (this.failure) throw this.failure;
            if (this.inFlight) await this.inFlight;
            else {
                const sent = await this.start();
                // Тот же ключ мог измениться во время запроса — успешный пакет есть прогресс.
                if ((this.dirty.size || this.editorDirty) && !sent && !this.failure) {
                    throw new Error("Объекты доски содержат несохранённые привязки.");
                }
            }
            this.clearTimer();
        }
        if (this.failure) throw this.failure;
    }

    reset(): void {
        this.clearTimer(); this.dirty.clear(); this.editorDirty = false; this.failure = null;
        this.persisted = new Map(this.options.getScene()); this.notify();
    }
    activate(): void { this.disposed = false; }
    dispose(): Promise<void> { const done = this.flushNow(); this.disposed = true; return done; }
    private schedule(): void {
        if (this.disposed) { void this.flushNow().catch(() => undefined); return; }
        this.clearTimer();
        this.timer = setTimeout(() => {
            this.timer = null;
            if (!this.inFlight) void this.start();
        }, this.options.debounceMs ?? SAVE_DEBOUNCE_MS);
    }
    private start(): Promise<boolean> {
        // Track the request before sendOnce clears dirty, while retaining the
        // synchronous first snapshot used by flush/undo during an in-flight create.
        let finish!: (value: boolean) => void;
        let fail!: (reason: unknown) => void;
        const tracked = new Promise<boolean>((resolve, reject) => { finish = resolve; fail = reject; });
        const run = tracked.finally(() => {
            if (this.inFlight === run) this.inFlight = null;
            this.notify();
            if ((this.dirty.size || this.editorDirty) && this.timer === null && !this.failure) this.schedule();
        });
        this.inFlight = run;
        this.notify();
        void this.sendOnce().then(finish, fail);
        return run;
    }
    private clearTimer(): void { if (this.timer !== null) { clearTimeout(this.timer); this.timer = null; } }
    private notify(): void { this.options.onStateChange?.({ pending: this.pending }); }

    buildOps(): { ops: BatchOp[]; sent: Set<string> } {
        const { registry } = this.options;
        const scene = this.options.getScene();
        const creates = new Set([...this.dirty].filter(key => scene.has(key) && registry.idOf(key) === undefined));
        const nodes: { key: string; op: BatchOp }[] = [];
        const connectors: { key: string; op: BatchOp }[] = [];
        const others: { key: string; op: BatchOp }[] = [];
        const sent = new Set<string>();
        for (const key of this.dirty) {
            const object = scene.get(key);
            const old = this.persisted.get(key);
            const id = registry.idOf(key);
            if (!object) {
                if (id === undefined) { sent.add(key); continue; }
                const op: BatchOp = key.startsWith("s")
                    ? { op: "delete_sticker", id, revision: old?.type === "sticker" ? old.sticker.revision : 1 }
                    : { op: "delete_shape", id };
                others.push({ key, op });
            } else if (object.type === "sticker") {
                const data = stickerToData(object);
                const op: BatchOp = id === undefined
                    ? { op: "create_sticker", client_id: key, data }
                    : { op: "update_sticker", id, revision: old?.type === "sticker" ? old.sticker.revision : object.sticker.revision, data };
                (id === undefined ? nodes : others).push({ key, op });
            } else {
                const data = shapeToData(object, registry, creates, id === undefined ? "create" : "update");
                if (data === "defer") continue;
                const op: BatchOp = id === undefined
                    ? { op: "create_shape", client_id: key, data }
                    : { op: "update_shape", id, data };
                (isConnectorKind(object.kind) ? connectors : nodes).push({ key, op });
            }
        }
        const ordered = [...nodes, ...connectors, ...others].slice(0, MAX_BATCH_OPS);
        // Новая привязка никогда не уходит раньше создания своей цели.
        const created = new Set(ordered.filter(item => item.op.op === "create_shape" || item.op.op === "create_sticker").map(item => item.key));
        const safe = ordered.filter(({ op }) => {
            if (op.op !== "create_shape" && op.op !== "update_shape") return true;
            return [op.data.start_ref, op.data.end_ref].every(ref => !ref?.client_id || created.has(ref.client_id));
        });
        for (const { key } of safe) sent.add(key);
        return { ops: safe.map(item => item.op), sent };
    }

    private async sendOnce(): Promise<boolean> {
        const { ops, sent } = this.buildOps();
        const snapshots = new Map([...sent].map(key => [key, this.options.getScene().get(key)]));
        for (const key of sent) this.dirty.delete(key);
        if (!ops.length && !this.editorDirty) return false;
        this.notify();
        const { registry } = this.options;
        try {
            const metaDirty = this.editorDirty;
            const creates = new Set(ops.filter(op => op.op === "create_shape" || op.op === "create_sticker").map(op => op.client_id));
            const editorState = metaDirty ? this.options.getEditorState?.(creates) : undefined;
            if (metaDirty && !editorState) throw new Error("Настройки редактора не готовы к сохранению.");
            this.editorDirty = false;
            const response = await this.options.send(ops, editorState);
            for (const [key, id] of Object.entries(response.id_map ?? {})) registry.bind(key, id);
            for (const [key, object] of snapshots) {
                if (!object) { this.persisted.delete(key); registry.unbind(key); continue; }
                if (object.type === "sticker") {
                    const result = response.stickers.find(item => item.id === registry.idOf(key));
                    this.persisted.set(key, result ? { ...object, id: result.id, sticker: result } : object);
                } else this.persisted.set(key, object);
            }
            this.options.onResponse?.(response, snapshots);
            return true;
        } catch (error) {
            this.failure = error; this.dirty.clear(); this.editorDirty = false; this.clearTimer();
            this.options.onFailure(error);
            return false;
        }
    }
}

export const pendingBoardSaves = new Map<number, Promise<void>>();
