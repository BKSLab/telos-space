import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, Input, Select } from "@/components/ui/Field";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { api, queryKeys } from "@/lib/api";
import { contextApi, type GitConnectionPage, type GitTaskLink, type GitTaskLinks } from "@/lib/projectKnowledge";

const STATUS: Record<string, string> = { open: "Открыто", closed: "Закрыто", merged: "Слито", commit: "Коммит", success: "Успех", failure: "Ошибка", pending: "Ожидает", queued: "В очереди", in_progress: "Выполняется", completed: "Завершено", inactive: "Неактивно", error: "Ошибка" };
interface Candidate { connection_id: number; repository_id: number; kind: "commit" | "pull_request"; commit_sha: string; pull_number: number | null; title: string; source_url: string; ambiguous: boolean }

function candidateIdentity(candidate: Candidate) {
    return candidate.kind === "pull_request" ? `PR #${candidate.pull_number}` : `коммит ${candidate.commit_sha}`;
}

export function TaskGitEvidence({ projectId, taskId, active = true }: { projectId: number; taskId: number; active?: boolean }) {
    const queryClient = useQueryClient();
    const base = `${contextApi.git(projectId)}/tasks/${taskId}`;
    const key = [...queryKeys.project(projectId), "git", "tasks", taskId, "links"];
    const [adding, setAdding] = useState(false);
    const [discovering, setDiscovering] = useState(false);
    const [connectionId, setConnectionId] = useState("");
    const [connectionOffset, setConnectionOffset] = useState(0);
    const [kind, setKind] = useState<"commit" | "pull_request">("pull_request");
    const [identity, setIdentity] = useState("");
    const [notice, setNotice] = useState("");
    const links = useQuery({ queryKey: key, queryFn: () => api.get<GitTaskLinks>(`${base}/links`), enabled: active, refetchInterval: active ? 15000 : false });
    const connections = useQuery({ queryKey: [...queryKeys.project(projectId), "git", "task-connections", connectionOffset], queryFn: () => api.get<GitConnectionPage>(`${contextApi.git(projectId)}/connections?limit=50&offset=${connectionOffset}`), enabled: active && adding });
    const candidates = useQuery({ queryKey: [...queryKeys.project(projectId), "git", "tasks", taskId, "candidates"], queryFn: () => api.get<{ items: Candidate[]; partial: boolean }>(`${base}/candidates`), enabled: active && discovering });
    const mutation = useMutation({
        mutationFn: (action: { type: "create"; data: { connection_id: number; kind: "commit" | "pull_request"; commit_sha?: string; pull_number?: number } } | { type: "remove" | "refresh"; link: GitTaskLink }) => {
            if (action.type === "create") return api.post(`${base}/links`, action.data);
            if (action.type === "refresh") return api.post(`${base}/links/${action.link.id}/refresh`);
            return api.delete(`${base}/links/${action.link.id}`);
        },
        onSuccess: async (_, action) => { setIdentity(""); setNotice(action.type === "refresh" ? "Проверка подтверждений запрошена." : "Связи обновлены."); await queryClient.invalidateQueries({ queryKey: key }); },
    });
    const create = (source: { connection_id: number; kind: "commit" | "pull_request"; commit_sha: string; pull_number: number | null }) => mutation.mutate({ type: "create", data: { connection_id: source.connection_id, kind: source.kind, ...(source.kind === "commit" ? { commit_sha: source.commit_sha } : { pull_number: source.pull_number! }) } });
    return <div className="mt-5 flex min-w-0 flex-col gap-3 border-t border-line pt-4 text-[13px]">
        <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium text-secondary">Код, проверки и выкладки</h3><Button size="sm" onClick={() => setAdding(!adding)}>Привязать изменение</Button></div>
        <p className="text-[12px] text-muted">Связи необязательны. Подтверждения относятся к указанному репозиторию, SHA и среде; merge и зелёный CI сами по себе не подтверждают прод.</p>
        {links.isPending && active && <Skeleton className="h-16" />}
        {(links.error || mutation.error) && <ErrorMessage message={(links.error ?? mutation.error)!.message} />}
        {notice && <p role="status" className="text-[12px] text-secondary">{notice}</p>}
        {links.data && !links.error && <>
            {links.data.partial && <p className="text-warning">Часть привязок недоступна или ещё не проверена.</p>}
            {links.data.items.length === 0 && <p className="text-muted">Git-подтверждений нет. Результат задачи можно зафиксировать в отчёте.</p>}
            {links.data.items.map(link => <Card key={link.id} variant="inset" className="flex min-w-0 flex-col gap-2 p-3">
                <a className="break-words font-medium text-accent hover:underline" href={link.source_url} target="_blank" rel="noopener noreferrer">{link.title || (link.kind === "pull_request" ? `PR #${link.pull_number}` : "Коммит")}</a>
                <p className="text-secondary">Репозиторий {link.repository_id} · {STATUS[link.status] ?? link.status}</p>
                <p className="break-all font-mono text-[12px] text-muted">{link.commit_sha}{link.merge_sha && <> · merge {link.merge_sha}</>}</p>
                {link.evidence_partial && <p className="text-[12px] text-warning">Подтверждения получены не полностью.</p>}
                {link.last_error_code && <p className="text-[12px] text-warning">Причина: {link.last_error_code}</p>}
                <ul className="space-y-2">{link.evidence.map(evidence => <li key={`${evidence.provider_type}-${evidence.provider_id}`} className="min-w-0 border-l-2 border-line pl-3">
                    <p className="text-secondary">{evidence.kind === "check" ? "Проверка CI" : `Выкладка · ${evidence.environment ?? "среда неизвестна"}`} · {STATUS[evidence.status] ?? evidence.status}{evidence.conclusion && ` · ${STATUS[evidence.conclusion] ?? evidence.conclusion}`}</p>
                    <p className="break-all font-mono text-[11px] text-muted">{evidence.commit_sha}</p>
                    {evidence.kind === "deployment" && <p className="text-[12px] text-muted">{evidence.is_current === true ? "Подтверждена текущая успешная выкладка в этой среде." : evidence.is_current === false ? "Эта выкладка больше не является текущей." : "Текущая версия среды не подтверждена."}</p>}
                    <a className="text-[12px] text-accent hover:underline" href={evidence.source_url} target="_blank" rel="noopener noreferrer">Источник подтверждения</a>
                </li>)}</ul>
                <div className="flex flex-wrap gap-2"><Button size="sm" disabled={mutation.isPending} onClick={() => mutation.mutate({ type: "refresh", link })}>Проверить подтверждения</Button><Button size="sm" disabled={mutation.isPending} onClick={() => mutation.mutate({ type: "remove", link })}>Отвязать</Button></div>
            </Card>)}
        </>}
        {adding && <form className="flex flex-col gap-3" onSubmit={event => { event.preventDefault(); create({ connection_id: Number(connectionId), kind, commit_sha: identity.trim(), pull_number: kind === "pull_request" ? Number(identity) : null }); }}>
            {connections.error && <ErrorMessage message={connections.error.message} />}
            <Field label="Подключённый репозиторий">{id => <Select id={id} required value={connectionId} onChange={event => setConnectionId(event.target.value)}><option value="">Выберите репозиторий</option>{connections.data?.items.filter(c => c.enabled).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</Select>}</Field>
            {connections.data && (connectionOffset > 0 || connections.data.next_offset !== null) && <div className="flex gap-2"><Button size="sm" disabled={connectionOffset === 0} onClick={() => { setConnectionId(""); setConnectionOffset(Math.max(0, connectionOffset - 50)); }}>Предыдущие репозитории</Button><Button size="sm" disabled={connections.data.next_offset === null} onClick={() => { if (connections.data.next_offset !== null) { setConnectionId(""); setConnectionOffset(connections.data.next_offset); } }}>Следующие репозитории</Button></div>}
            <Field label="Тип изменения">{id => <Select id={id} value={kind} onChange={event => { setKind(event.target.value as typeof kind); setIdentity(""); }}><option value="pull_request">Pull request</option><option value="commit">Коммит</option></Select>}</Field>
            <Field label={kind === "pull_request" ? "Номер PR" : "Полный SHA коммита"}>{id => <Input id={id} required value={identity} onChange={event => setIdentity(event.target.value)} pattern={kind === "pull_request" ? "[1-9][0-9]*" : "[a-fA-F0-9]{40,64}"} />}</Field>
            <Button type="submit" disabled={mutation.isPending || !connectionId || !identity.trim()}>Добавить связь</Button>
        </form>}
        <details onToggle={event => setDiscovering(event.currentTarget.open)}><summary className="touch-target cursor-pointer text-accent">Найти возможные связи по ключу задачи</summary>
            {candidates.isFetching && discovering && <p role="status">Ищем кандидатов…</p>}
            {candidates.error && <ErrorMessage message={candidates.error.message} />}
            {candidates.data && !candidates.error && <>
                {candidates.data.partial && <p className="text-warning">Проверена только доступная часть изменений.</p>}
                {candidates.data.items.length === 0 && <p className="text-muted">Кандидатов в проверенной части нет.</p>}
                <ul className="space-y-2">{candidates.data.items.map(candidate => <li key={`${candidate.connection_id}-${candidate.kind}-${candidate.pull_number ?? candidate.commit_sha}`} className="flex flex-wrap items-center gap-2 py-2">
                    <div className="min-w-0 flex-1">
                        <p className="break-words text-secondary">{candidate.title}{candidate.ambiguous && " · Упоминает несколько задач"}</p>
                        <p className="break-all text-[12px] text-muted">Репозиторий {candidate.repository_id} · {candidateIdentity(candidate)}</p>
                        <a className="inline-flex min-h-11 items-center break-all text-[12px] text-accent hover:underline" href={candidate.source_url} target="_blank" rel="noopener noreferrer">{candidate.source_url}</a>
                    </div>
                    <Button size="sm" aria-label={`Привязать ${candidateIdentity(candidate)} из репозитория ${candidate.repository_id}`} disabled={mutation.isPending} onClick={() => create(candidate)}>Привязать</Button>
                </li>)}</ul>
            </>}
        </details>
    </div>;
}
