import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { newUuid } from "./uuid";

export interface PendingDraftUpload {
    id: string;
    name: string;
    status: "uploading" | "failed";
    error?: string;
}
export interface DraftUploadContext {
    cancelled: () => boolean;
    adoptScope: (scope: string) => void;
}
interface UploadOperation<T> {
    upload: (file: File, context: DraftUploadContext) => Promise<T>;
    attach: (value: T) => void;
    discard: (value: T) => Promise<unknown>;
}
interface UploadJob<T> {
    id: string;
    file: File;
    scope: string;
    cancelled: boolean;
    status: PendingDraftUpload["status"];
    error?: string;
    operation: UploadOperation<T>;
}

/** Completed drafts stay in their store. Only unfinished uploads belong to this view. */
export function useDraftUploads<T>(scope: string) {
    const jobs = useRef(new Map<string, UploadJob<T>>());
    const removals = useRef(new Map<string, string>());
    const owner = useRef(scope);
    const mounted = useRef(true);
    const [entries, setEntries] = useState<PendingDraftUpload[]>([]);
    const [removing, setRemoving] = useState(false);
    const publish = useCallback(() => {
        if (mounted.current) {
            setEntries([...jobs.current.values()].map(job => ({ id: job.id, name: job.file.name, status: job.status, error: job.error })));
            setRemoving([...removals.current.values()].some(value => value === owner.current));
        }
    }, []);
    const cancel = useCallback((id: string) => {
        const job = jobs.current.get(id);
        if (!job) return;
        job.cancelled = true;
        jobs.current.delete(id);
        publish();
    }, [publish]);
    const cancelAll = useCallback(() => {
        for (const job of jobs.current.values()) job.cancelled = true;
        jobs.current.clear();
        publish();
    }, [publish]);
    useLayoutEffect(() => {
        if (owner.current !== scope) { owner.current = scope; cancelAll(); }
    }, [scope, cancelAll]);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; cancelAll(); };
    }, [cancelAll]);

    async function run(job: UploadJob<T>) {
        job.status = "uploading"; job.error = undefined;
        publish();
        try {
            // Do not abort the HTTP transfer: its returned UUID is needed to clean a late success.
            const value = await job.operation.upload(job.file, {
                cancelled: () => job.cancelled,
                adoptScope: next => {
                    if (job.cancelled || owner.current !== job.scope) return;
                    const previous = job.scope;
                    owner.current = next;
                    for (const entry of jobs.current.values()) if (entry.scope === previous) entry.scope = next;
                },
            });
            if (job.cancelled || !mounted.current || job.scope !== owner.current) {
                try { await job.operation.discard(value); } catch { /* Server TTL covers disconnected cleanup. */ }
            } else {
                job.operation.attach(value);
                jobs.current.delete(job.id);
            }
        } catch (cause) {
            if (!job.cancelled && mounted.current && job.scope === owner.current) {
                job.status = "failed";
                job.error = (cause as Error).message || "Не удалось загрузить файл.";
            }
        }
        publish();
    }
    function add(files: File[], operation: UploadOperation<T>) {
        for (const file of files) {
            const job: UploadJob<T> = { id: newUuid(), file, scope: owner.current, cancelled: false, status: "uploading", operation };
            jobs.current.set(job.id, job);
            void run(job);
        }
    }
    function retry(id: string) {
        const job = jobs.current.get(id);
        if (job?.status === "failed") void run(job);
    }
    async function remove(id: string, operation: () => Promise<void>) {
        const key = `${owner.current}:${id}`;
        if (removals.current.has(key)) return;
        const currentScope = owner.current;
        removals.current.set(key, currentScope);
        publish();
        try { await operation(); }
        catch (cause) { if (mounted.current && owner.current === currentScope) throw cause; }
        finally { removals.current.delete(key); publish(); }
    }
    return { entries, blocked: entries.length > 0 || removing, removing, count: entries.length, add, cancel, retry, remove,
        isBlocked: () => jobs.current.size > 0 || [...removals.current.values()].some(value => value === owner.current) };
}

export function draftUploadProblem(files: File[], currentCount: number): string | null {
    if (currentCount + files.length > 5) return "В одном сообщении можно отправить до 5 файлов.";
    if (files.some(file => !file.size || file.size > 10 * 1024 * 1024)) return "Нужны непустые файлы размером до 10 МБ.";
    return null;
}
