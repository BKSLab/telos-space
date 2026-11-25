import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/States";
import type { GitSnapshotInfo } from "@/lib/projectKnowledge";
import { useRepositoryQuery } from "./repositoryApi";

interface Comparison { base_sha: string; head_sha: string; files_complete: boolean; commits_complete: boolean; incomplete_reasons: string[]; files: { filename: string; previous_filename: string | null; status: string; patch: string | null; patch_truncated: boolean }[] }
export function RepositoryChanges({ project, connection, sha, snapshots, onOpen }: { project: number; connection: number; sha: string; snapshots: GitSnapshotInfo[]; onOpen: (path: string, sha: string) => void }) {
    const [base, setBase] = useState("");
    const result = useRepositoryQuery<Comparison>({ project, connection, sha, kind: "compare", params: { base_sha: base, head_sha: sha }, enabled: Boolean(base) && base !== sha });
    return <section className="min-w-0 space-y-3" aria-label="Изменения версий">
        <label className="flex flex-wrap items-center gap-2 text-[13px] text-secondary">Сравнить с версией<select aria-label="Базовая версия" className="control-surface max-w-full rounded-md p-2 font-mono text-[12px]" value={base} onChange={event => setBase(event.target.value)}><option value="">Выберите SHA</option>{snapshots.filter(item => item.commit_sha !== sha).map(item => <option key={item.commit_sha} value={item.commit_sha}>{item.commit_sha}</option>)}</select></label>
        <p className="break-all font-mono text-[12px] text-muted">Head: {sha}</p>
        {snapshots.length < 2 && <p className="text-[13px] text-muted">Для сравнения нужны две доступные версии одного репозитория.</p>}
        {result.isFetching && <p role="status" className="text-muted">Читаем изменения…</p>}
        {result.error && <ErrorMessage message={result.error.message} />}
        {result.data && !result.error && <>
            {(!result.data.files_complete || !result.data.commits_complete) && <p className="text-[12px] text-warning">Сравнение неполное: {result.data.incomplete_reasons.join(", ") || "ограничение upstream"}.</p>}
            {result.data.files.length === 0 && <p className="text-muted">В полученной области изменений нет.</p>}
            <ul className="divide-y divide-line-subtle">{result.data.files.map(file => <li key={file.filename} className="space-y-2 py-3">
                <p className="break-all text-[13px] text-primary">{file.status} · {file.filename}</p>
                {file.previous_filename && <p className="break-all text-[12px] text-muted">Прежний путь: {file.previous_filename}</p>}
                <Button size="sm" onClick={() => onOpen(file.status === "removed" ? file.previous_filename ?? file.filename : file.filename, file.status === "removed" ? result.data.base_sha : sha)}>Открыть исходник</Button>
                {file.patch ? <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded border border-line-subtle p-2 font-mono text-[12px] text-secondary">{file.patch}</pre> : <p className="text-[12px] text-muted">Текст diff не предоставлен.</p>}
                {file.patch_truncated && <p className="text-[12px] text-warning">Patch обрезан.</p>}
            </li>)}</ul>
        </>}
    </section>;
}
