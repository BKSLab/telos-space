import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch } from "lucide-react";
import { Button, LinkButton } from "@/components/ui/Button";
import { Card, Section, SettingsPanel } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { api, ApiError, queryKeys } from "@/lib/api";
import { validRepositoryDrafts, type RepositoryDraft } from "@/lib/github";
import { contextApi, GIT_STATES, type GitConnection, type GitConnectionPage, type GitFilePage, type GitSourceHit } from "@/lib/projectKnowledge";
import { useGitHubPause } from "@/lib/useGitHubPause";
import { useRepositoryConnections } from "@/lib/useRepositoryConnections";
import { GitHubRepositoryPicker } from "./GitHubRepositoryPicker";
import { RepositoryConnectionProgress } from "./RepositoryConnectionProgress";

const gitConnectionsKey = (projectId: number) => [...queryKeys.project(projectId), "git", "connections"];
const connectionPath = (projectId: number, id: number) => `${contextApi.git(projectId)}/connections/${id}`;

function connectionStatus(connection: GitConnection) {
    if (connection.last_error_code === "rate_limited") return "Индексация приостановлена";
    if (connection.last_error_code === "github_profile_pending") return "Ожидает проверки доступа";
    return GIT_STATES[connection.state] ?? connection.state;
}

function RepositoryPreview({ page }: { page: GitConnectionPage }) {
    if (page.total === 0) return <p className="text-[13px] text-muted">Репозитории не подключены.</p>;
    const attention = (connection: GitConnection) => ["error", "access_lost"].includes(connection.state) || connection.last_error_code === "rate_limited";
    const displayed = [...page.items].sort((a, b) => Number(attention(b)) - Number(attention(a))).slice(0, 3);
    return <ul className="divide-y divide-line-subtle text-[13px]">
        {displayed.map(connection => <li key={connection.id} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 py-2 first:pt-0 last:pb-0">
            <div className="min-w-0">
                <p className="break-words font-medium text-secondary">{connection.name}</p>
                <p className="break-all text-[12px] text-muted">{connection.ref.replace(/^heads\//, "")}</p>
            </div>
            <span className={attention(connection) ? "text-warning" : "text-muted"}>{connectionStatus(connection)}</span>
        </li>)}
        {page.total > displayed.length && <li className="pt-2 text-muted">Ещё {page.total - displayed.length}</li>}
    </ul>;
}

function GitConnectionCard({ connection, canEdit, refresh }: { connection: GitConnection; canEdit: boolean; refresh: () => Promise<unknown> }) {
    const [draft, setDraft] = useState<{ name: string; purpose: string; ref: string; expected_revision: number; share_private_with_project: boolean } | null>(null);
    const [notice, setNotice] = useState("");
    const rateLimited = connection.last_error_code === "rate_limited";
    const profilePending = connection.last_error_code === "github_profile_pending";
    const retryAt = rateLimited && connection.next_poll_at ? Date.parse(connection.next_poll_at) : NaN;
    const paused = useGitHubPause(Number.isFinite(retryAt) ? retryAt : null);
    const path = connectionPath(connection.project_id, connection.id);
    const mutation = useMutation({
        mutationFn: async (action: "save" | "sync" | "disable" | "enable") => {
            if (action === "save" && draft) return api.patch(path, draft);
            if (action === "sync") return api.post(`${path}/sync`);
            if (action === "disable") return api.delete(path);
            if (action === "enable") return api.patch(path, { expected_revision: connection.revision, enabled: true });
        },
        onSuccess: async (_, action) => {
            setDraft(null);
            setNotice(action === "sync" ? "Обновление поставлено в очередь." : "Изменения сохранены.");
            await refresh();
        },
    });
    const beginEdit = () => { mutation.reset(); setNotice(""); setDraft({ name: connection.name, purpose: connection.purpose, ref: connection.ref, expected_revision: connection.revision, share_private_with_project: connection.share_private_with_project }); };
    return <Card variant="inset" className="flex min-w-0 flex-col gap-3 p-4">
        <div className="flex flex-wrap justify-between gap-2">
            <div className="min-w-0"><h3 className="break-words font-medium text-secondary">{connection.name}</h3>
                <p className="break-all text-[12px] text-muted">{connection.owner}/{connection.repo} · {connection.ref} · {connection.is_private ? "Закрытый" : "Публичный"}</p></div>
            <p className="text-[12px] text-secondary">{connectionStatus(connection)}</p>
        </div>
        {connection.purpose && <p className="break-words text-[13px] text-secondary">{connection.purpose}</p>}
        <dl className="grid min-w-0 gap-1 text-[12px]">
            <div><dt className="text-muted">Обнаруженная версия</dt><dd className="break-all font-mono text-secondary">{connection.observed_sha ?? "Не проверена"}</dd></div>
            <div><dt className="text-muted">Проиндексированная версия</dt><dd className="break-all font-mono text-secondary">{connection.indexed_sha ?? "Индексация не завершена"}</dd></div>
        </dl>
        <p className="text-[12px] text-muted">Файлов: {connection.coverage.indexed_files} из {connection.coverage.total_files}; исключено {connection.coverage.excluded_files}; ошибок {connection.coverage.failed_files}.
            {connection.coverage.truncated && " Дерево получено не полностью."}
            {connection.checked_at && <> Проверено: <time dateTime={connection.checked_at}>{new Date(connection.checked_at).toLocaleString("ru-RU")}</time>.</>}</p>
        {rateLimited && <p role="status" className="text-[12px] text-warning">
            {paused ? <>Лимит GitHub исчерпан. Следующая проверка после <time dateTime={connection.next_poll_at ?? undefined}>{new Date(retryAt).toLocaleString("ru-RU")}</time>.</> : "Лимит GitHub исчерпан. Можно запросить повторную проверку."}
        </p>}
        {profilePending && <p role="status" className="text-[12px] text-muted">Ожидает проверки доступа к GitHub.</p>}
        {!rateLimited && !profilePending && connection.last_error_code && <p role="status" className="text-[12px] text-warning">Причина: {connection.last_error_code}. Версия и доступ будут проверены при следующем обновлении.</p>}
        {notice && <p role="status" className="text-[12px] text-secondary">{notice}</p>}
        {mutation.error && <ErrorMessage message={mutation.error.message} />}
        {mutation.error instanceof ApiError && mutation.error.status === 409 && <Button size="sm" onClick={async () => { await refresh(); setDraft(null); mutation.reset(); }}>Загрузить актуальные настройки</Button>}
        {canEdit && <>
            <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled={mutation.isPending} onClick={beginEdit}>Настроить</Button>
                <Button size="sm" disabled={!connection.enabled || mutation.isPending || paused} onClick={() => mutation.mutate("sync")}>{rateLimited ? "Повторить проверку" : "Проверить обновления"}</Button>
                <Button size="sm" disabled={mutation.isPending} onClick={() => mutation.mutate(connection.enabled ? "disable" : "enable")}>{connection.enabled ? "Отключить" : "Включить"}</Button>
            </div>
            {connection.state === "access_lost" && <LinkButton size="sm" to="/profile?tab=github">Настроить доступ к GitHub</LinkButton>}
            {draft && <form className="flex flex-col gap-3" onSubmit={event => { event.preventDefault(); mutation.mutate("save"); }}>
                <Field label="Название источника">{id => <Input id={id} value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} required maxLength={200} />}</Field>
                <Field label="Назначение">{id => <Input id={id} value={draft.purpose} onChange={event => setDraft({ ...draft, purpose: event.target.value })} maxLength={1000} />}</Field>
                <Field label="Ветка или ref">{id => <Input id={id} value={draft.ref} onChange={event => setDraft({ ...draft, ref: event.target.value })} required />}</Field>
                {connection.is_private && <label className="touch-target flex items-center gap-2 text-[13px] text-secondary"><input type="checkbox" checked={draft.share_private_with_project} onChange={event => setDraft({ ...draft, share_private_with_project: event.target.checked })} />Разрешить команде проекта читать этот закрытый репозиторий</label>}
                <p className="text-[12px] text-muted">Изменение ref обновит только этот источник. При конфликте перечитайте настройки перед повторным сохранением.</p>
                <div className="flex gap-2"><Button type="submit" disabled={mutation.isPending}>Сохранить источник</Button><Button disabled={mutation.isPending} onClick={() => { setDraft(null); mutation.reset(); }}>Отменить</Button></div>
            </form>}
        </>}
    </Card>;
}

export function ProjectGitSources({ projectId, canEdit, collapsible = false }: {
    projectId: number;
    canEdit: boolean;
    collapsible?: boolean;
}) {
    const queryClient = useQueryClient();
    const [offset, setOffset] = useState(0);
    const [adding, setAdding] = useState(false);
    const [repositories, setRepositories] = useState<RepositoryDraft[]>([]);
    const git = useRepositoryConnections();
    const [queryText, setQueryText] = useState("");
    const [searchText, setSearchText] = useState("");
    const [mapOpen, setMapOpen] = useState(false);
    const [selected, setSelected] = useState<{ hit: Pick<GitSourceHit, "connection_id" | "commit_sha" | "path">; offset: number } | null>(null);
    const refresh = () => Promise.all([
        queryClient.invalidateQueries({ queryKey: gitConnectionsKey(projectId) }),
        queryClient.invalidateQueries({ queryKey: [...queryKeys.project(projectId), "git", "search"] }),
        queryClient.invalidateQueries({ queryKey: [...queryKeys.project(projectId), "git", "file"] }),
        queryClient.invalidateQueries({ queryKey: [...queryKeys.project(projectId), "git", "map"] }),
    ]);
    const connections = useQuery({ queryKey: [...gitConnectionsKey(projectId), offset], queryFn: () => api.get<GitConnectionPage>(`${contextApi.git(projectId)}/connections?offset=${offset}&limit=20`), refetchInterval: 15000 });
    const search = useQuery({ queryKey: [...queryKeys.project(projectId), "git", "search", searchText], queryFn: () => api.get<{ items: GitSourceHit[]; partial: boolean }>(`${contextApi.git(projectId)}/search?q=${encodeURIComponent(searchText)}&limit=10`), enabled: Boolean(searchText) });
    const codeMap = useQuery({
        queryKey: [...queryKeys.project(projectId), "git", "map"],
        queryFn: () => api.get<{ manifest: GitConnection[]; components: { connection_id: number; repository_id: number; commit_sha: string; file_id: number; path: string; kind: string }[]; partial: boolean }>(`${contextApi.git(projectId)}/map`),
        enabled: mapOpen,
    });
    const file = useQuery({
        queryKey: [...queryKeys.project(projectId), "git", "file", selected?.hit.connection_id, selected?.hit.commit_sha, selected?.hit.path, selected?.offset],
        queryFn: () => api.get<GitFilePage>(`${connectionPath(projectId, selected!.hit.connection_id)}/file?sha=${selected!.hit.commit_sha}&path=${encodeURIComponent(selected!.hit.path)}&offset=${selected!.offset}&max_chars=8000`),
        enabled: selected !== null, staleTime: 0,
    });
    async function connect(retry: boolean) {
        if (retry) await git.retry(projectId);
        else await git.start(projectId, repositories);
        await refresh();
    }
    const action = canEdit && !adding && <Button size="sm" onClick={() => setAdding(true)}>Добавить репозитории</Button>;
    const content = <>
        <p className="text-[13px] text-muted">У каждого источника своя ветка и версия. Арис читает код и документацию; подключение не даёт прав изменять репозиторий.</p>
        {connections.isPending && <Skeleton className="h-24" />}
        {connections.error && <ErrorMessage message={connections.error.message} action={<Button onClick={() => void connections.refetch()}>Повторить</Button>} />}
        {adding && canEdit && <Card className="space-y-3 p-4">
            {git.items.length === 0 ? <>
                <GitHubRepositoryPicker value={repositories} onChange={setRepositories} disabled={git.isPending} />
                <Button variant="primary" disabled={git.isPending || repositories.length === 0 || !validRepositoryDrafts(repositories)} onClick={() => void connect(false)}>Подключить выбранные</Button>
            </> : <>
                <RepositoryConnectionProgress items={git.items} retryAt={git.retryAt} pending={git.isPending} onRetry={() => void connect(true)} />
                {!git.isPending && git.retryAt === null && git.items.some(item => item.state === "failed") &&
                    <Button onClick={() => { setRepositories(git.items.filter(item => item.state === "failed").map(item => item.draft)); git.reset(); }}>Изменить неподключённые источники</Button>}
            </>}
            <Button className="ml-2" disabled={git.isPending} onClick={() => { setAdding(false); setRepositories([]); git.reset(); }}>{git.items.length ? "Закрыть" : "Отмена"}</Button>
        </Card>}
        {!connections.error && connections.data?.total === 0 && <p className="text-[13px] text-muted">Репозитории не подключены. Контекст из паспорта и документов доступен без Git.</p>}
        <div className="grid min-w-0 gap-3">{!connections.error && connections.data?.items.map(connection => <GitConnectionCard key={connection.id} connection={connection} canEdit={canEdit} refresh={refresh} />)}</div>
        {connections.data && !connections.error && (offset > 0 || connections.data.next_offset !== null) && <nav aria-label="Страницы репозиториев" className="flex gap-2"><Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Назад</Button><Button disabled={connections.data.next_offset === null} onClick={() => connections.data.next_offset !== null && setOffset(connections.data.next_offset)}>Дальше</Button></nav>}
        <details onToggle={event => setMapOpen(event.currentTarget.open)}>
            <summary className="touch-target cursor-pointer text-accent">Карта компонентов репозиториев</summary>
            {codeMap.isFetching && mapOpen && <p role="status" className="text-[13px] text-muted">Читаем доступную карту…</p>}
            {codeMap.error && <ErrorMessage message={codeMap.error.message} />}
            {codeMap.data && !codeMap.error && <div className="mt-2 flex min-w-0 flex-col gap-2 text-[13px]">
                <p className="text-muted">Набор версий состоит из {codeMap.data.manifest.length} независимых подключений. Имя файла указывает на возможную роль; поведение проверяйте по исходнику.</p>
                {codeMap.data.partial && <p className="text-warning">Карта неполная: часть источников или компонентов не обработана.</p>}
                <ul className="space-y-1">{codeMap.data.components.slice(0, 50).map(component => <li key={`${component.connection_id}-${component.file_id}`}><Button size="sm" className="max-w-full justify-start whitespace-normal text-left" onClick={() => setSelected({ hit: component, offset: 0 })}>{connections.data?.items.find(connection => connection.id === component.connection_id)?.name ?? `Репозиторий ${component.repository_id}`} · {component.path}</Button><p className="break-all text-[11px] text-muted">{component.kind} · {component.commit_sha}</p></li>)}</ul>
                {codeMap.data.components.length > 50 && <p className="text-muted">Показаны первые 50 компонентов. Используйте поиск для точного отбора.</p>}
            </div>}
        </details>
        <form className="flex flex-wrap items-end gap-2" onSubmit={event => { event.preventDefault(); const value = queryText.trim(); if (value) { if (value === searchText) void search.refetch(); else setSearchText(value); } }}>
            <Field className="min-w-0 flex-1" label="Найти код и документацию">{id => <Input id={id} value={queryText} maxLength={500} onChange={event => setQueryText(event.target.value)} />}</Field><Button type="submit" disabled={!queryText.trim()}>Найти в репозиториях</Button>
        </form>
        {search.isFetching && <p role="status" className="text-[13px] text-muted">Ищем доступные фрагменты…</p>}
        {search.error && <ErrorMessage message={search.error.message} />}
        {search.data && !search.error && !connections.error && <>
            {search.data.partial && <p className="text-[12px] text-warning">Поиск неполный: часть репозиториев не готова или недоступна.</p>}
            {search.data.items.length === 0 && <p className="text-[13px] text-muted">В доступной части индекса совпадений нет.</p>}
            <ul className="space-y-2">{search.data.items.map(hit => <li key={`${hit.connection_id}-${hit.file_id}`}><Button className="max-w-full justify-start whitespace-normal text-left" onClick={() => setSelected({ hit, offset: 0 })}>{connections.data?.items.find(c => c.id === hit.connection_id)?.name ?? `Репозиторий ${hit.repository_id}`} · {hit.path}</Button><p className="mt-1 break-words text-[12px] text-muted">{hit.excerpt}</p></li>)}</ul>
        </>}
        <Modal isOpen={selected !== null} onOpenChange={open => { if (!open) setSelected(null); }} title={selected?.hit.path ?? "Исходный файл"}>
            {file.isFetching && <p role="status">Читаем выбранную версию…</p>}
            {file.error && <ErrorMessage message={file.error.message} />}
            {file.data && !file.error && !file.isFetching && <div className="flex min-w-0 flex-col gap-3">
                <p className="break-all font-mono text-[12px] text-muted">{file.data.commit_sha} · строки {file.data.line_start}–{file.data.line_end}</p>
                {(file.data.partial || file.data.stale) && <p className="text-[12px] text-warning">Версия или охват индекса неполны. Вывод относится только к прочитанному фрагменту.</p>}
                <pre className="max-h-[55vh] overflow-auto whitespace-pre-wrap break-all rounded-md border border-line p-3 text-[12px] text-secondary">{file.data.text}</pre>
                {file.data.next_offset !== null && <Button onClick={() => selected && file.data.next_offset !== null && setSelected({ ...selected, offset: file.data.next_offset })}>Читать дальше</Button>}
            </div>}
        </Modal>
    </>;
    if (!collapsible) return <Section title="Репозитории проекта" action={action}>{content}</Section>;
    return <SettingsPanel title="Репозитории проекта" icon={<GitBranch size={16} aria-hidden="true" />}
        controlLabel={canEdit ? "Управлять репозиториями" : "Посмотреть репозитории"}
        action={action}
        preview={connections.isPending ? <Skeleton className="h-8 w-full" />
            : connections.error ? <p className="text-[13px] text-danger">Не удалось загрузить репозитории.</p>
            : <RepositoryPreview page={connections.data} />}
    >{content}</SettingsPanel>;
}
