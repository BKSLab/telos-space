import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch } from "lucide-react";
import { Button, LinkButton } from "@/components/ui/Button";
import { Card, Section, SettingsPanel } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { api, ApiError, endpoints, queryKeys } from "@/lib/api";
import type { ProjectStage } from "@/lib/types";
import { validRepositoryDrafts, type RepositoryDraft } from "@/lib/github";
import { contextApi, type GitConnection, type GitConnectionPage } from "@/lib/projectKnowledge";
import { useGitHubPause } from "@/lib/useGitHubPause";
import { useRepositoryConnections } from "@/lib/useRepositoryConnections";
import { GitHubRepositoryPicker } from "./GitHubRepositoryPicker";
import { RepositoryConnectionProgress } from "./RepositoryConnectionProgress";
import { GitIndexBadge, GitIndexDetails, GitIndexSummary } from "./GitIndexStatus";
import { GitWebhookSettings } from "./GitWebhookSettings";

const gitConnectionsKey = (projectId: number) => [...queryKeys.project(projectId), "git", "connections"];
const connectionPath = (projectId: number, id: number) => `${contextApi.git(projectId)}/connections/${id}`;

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
            <GitIndexBadge connection={connection} />
        </li>)}
        {page.total !== null && page.total > displayed.length && <li className="pt-2 text-muted">Ещё {page.total - displayed.length}</li>}
    </ul>;
}

function GitConnectionCard({ connection, canEdit, refresh }: { connection: GitConnection; canEdit: boolean; refresh: () => Promise<unknown> }) {
    const [draft, setDraft] = useState<{ name: string; purpose: string; ref: string; expected_revision: number; share_private_with_project: boolean; sync_stage_ids: number[] } | null>(null);
    const [notice, setNotice] = useState("");
    const rateLimited = connection.last_error_code === "rate_limited";
    const profilePending = connection.last_error_code === "github_profile_pending";
    const retryAt = rateLimited && connection.next_poll_at ? Date.parse(connection.next_poll_at) : NaN;
    const paused = useGitHubPause(Number.isFinite(retryAt) ? retryAt : null);
    const path = connectionPath(connection.project_id, connection.id);
    const stages = useQuery({ queryKey: queryKeys.stages(connection.project_id), queryFn: () => api.get<ProjectStage[]>(endpoints.projectStages(connection.project_id)), enabled: canEdit && draft !== null });
    const stageOptions = Array.isArray(stages.data) ? stages.data : null;
    const mutation = useMutation({
        mutationFn: async (action: "save" | "sync" | "disable" | "enable") => {
            if (action === "save" && draft) {
                const settings: Record<string, unknown> = { expected_revision: draft.expected_revision };
                for (const field of ["name", "purpose", "ref", "share_private_with_project"] as const) {
                    if (draft[field] !== connection[field]) settings[field] = draft[field];
                }
                if (stageOptions !== null) {
                    const selected = draft.sync_stage_ids.filter(id => stageOptions.some(stage => stage.id === id));
                    const saved = connection.sync_stage_ids ?? [];
                    if (selected.length !== saved.length || selected.some(id => !saved.includes(id))) settings.sync_stage_ids = selected;
                }
                return api.patch(path, settings);
            }
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
    const beginEdit = () => { mutation.reset(); setNotice(""); setDraft({ name: connection.name, purpose: connection.purpose, ref: connection.ref, expected_revision: connection.revision, share_private_with_project: connection.share_private_with_project, sync_stage_ids: connection.sync_stage_ids ?? [] }); };
    return <Card variant="inset" className="flex min-w-0 flex-col gap-3 p-4">
        <div className="flex flex-col items-start gap-2 sm:flex-row sm:justify-between">
            <div className="flex min-w-0 flex-1 items-start gap-2.5"><GitBranch size={18} className="mt-0.5 shrink-0 text-accent" aria-hidden="true" /><div className="min-w-0"><h3 className="break-words font-medium text-secondary">{connection.name}</h3>
                <p className="break-all text-[12px] text-muted">{connection.owner}/{connection.repo} · {connection.ref} · {connection.is_private ? "Закрытый" : "Публичный"}</p></div></div>
            <GitIndexBadge connection={connection} />
        </div>
        {connection.purpose && <p className="break-words text-[13px] text-secondary">{connection.purpose}</p>}
        <GitIndexDetails connection={connection} />
        {rateLimited && <p role="status" className="text-[12px] text-warning">
            {paused ? <>Лимит GitHub исчерпан. Следующая проверка после <time dateTime={connection.next_poll_at ?? undefined}>{new Date(retryAt).toLocaleString("ru-RU")}</time>.</> : "Лимит GitHub исчерпан. Можно запросить повторную проверку."}
        </p>}
        {profilePending && <p role="status" className="text-[12px] text-muted">Ожидает проверки доступа к GitHub.</p>}
        {!rateLimited && !profilePending && connection.last_error_code && <p role="status" className="text-[12px] text-warning">Причина: {connection.last_error_code}. Версия и доступ будут проверены при следующем обновлении.</p>}
        {notice && <p role="status" className="text-[12px] text-secondary">{notice}</p>}
        {mutation.error && <ErrorMessage message={mutation.error.message} />}
        {mutation.error instanceof ApiError && mutation.error.status === 409 && <Button size="sm" onClick={async () => { await refresh(); setDraft(null); mutation.reset(); }}>Загрузить актуальные настройки</Button>}
        <LinkButton size="sm" to={`../knowledge?view=code&connection=${connection.id}`}>Исследовать код</LinkButton>
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
                <fieldset className="space-y-2 border-t border-line-subtle pt-3" disabled={mutation.isPending}>
                    <legend className="pt-3 text-[13px] font-medium text-secondary">Проверять Git при переходе задачи</legend>
                    <p className="text-[12px] text-muted">Выберите стадии запуска для этого репозитория. Без выбранных стадий правило выключено. Переименование стадии сохраняет настройку.</p>
                    {stages.isPending && <p className="text-[12px] text-muted">Загружаем стадии…</p>}
                    {stages.error && <ErrorMessage message="Не удалось загрузить стадии. Сохранённое правило остаётся без изменений." action={<Button size="sm" onClick={() => void stages.refetch()}>Повторить загрузку стадий</Button>} />}
                    {stageOptions !== null && <div className="flex flex-wrap gap-x-5 gap-y-1">{stageOptions.map(stage => <label key={stage.id} className="touch-target flex min-w-0 items-center gap-2 text-[13px] text-secondary"><input type="checkbox" checked={draft.sync_stage_ids.includes(stage.id)} onChange={event => setDraft({ ...draft, sync_stage_ids: event.target.checked ? [...draft.sync_stage_ids, stage.id] : draft.sync_stage_ids.filter(id => id !== stage.id) })} /><span className="break-words">{stage.name}</span></label>)}</div>}
                    {stageOptions !== null && draft.sync_stage_ids.some(id => !stageOptions.some(stage => stage.id === id)) && <p className="text-[12px] text-warning">Удалённые стадии будут исключены из правила при сохранении.</p>}
                    <p className="text-[12px] text-muted">При явной Git-связи задачи проверяются только связанные репозитории. Если связей нет — подключённые источники с таким правилом. Переход задачи не подтверждает push или выкладку.</p>
                </fieldset>
                <p className="text-[12px] text-muted">Изменение ref обновит только этот источник. При конфликте перечитайте настройки перед повторным сохранением.</p>
                <div className="flex gap-2"><Button type="submit" disabled={mutation.isPending}>Сохранить источник</Button><Button disabled={mutation.isPending} onClick={() => { setDraft(null); mutation.reset(); }}>Отменить</Button></div>
            </form>}
            {!draft && <GitWebhookSettings connection={connection} refresh={refresh} />}
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
    const refresh = () => Promise.all([
        queryClient.invalidateQueries({ queryKey: gitConnectionsKey(projectId) }),
        queryClient.invalidateQueries({ queryKey: [...queryKeys.project(projectId), "git", "search"] }),
        queryClient.invalidateQueries({ queryKey: [...queryKeys.project(projectId), "git", "file"] }),
        queryClient.invalidateQueries({ queryKey: [...queryKeys.project(projectId), "git", "map"] }),
        queryClient.invalidateQueries({ queryKey: [...queryKeys.project(projectId), "git", "tree"] }),
    ]);
    const connections = useQuery({ queryKey: [...gitConnectionsKey(projectId), offset], queryFn: () => api.get<GitConnectionPage>(`${contextApi.git(projectId)}/connections?offset=${offset}&limit=20`), refetchInterval: 15000 });
    async function connect(retry: boolean) {
        if (retry) await git.retry(projectId);
        else await git.start(projectId, repositories);
        await refresh();
    }
    const action = canEdit && !adding && <Button size="sm" onClick={() => setAdding(true)}>Добавить репозитории</Button>;
    const content = <>
        <p className="text-[13px] text-muted">У каждого источника своя ветка и версия. В каталог попадают все файлы выбранного ref, без фильтров по пути и содержимому. Арис читает доступный текст; двоичные файлы и внешние объекты учитываются по метаданным. Подключение не даёт прав изменять репозиторий.</p>
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
        {connections.data && !connections.error && <GitIndexSummary page={connections.data} />}
        <div className="grid min-w-0 gap-3">{!connections.error && connections.data?.items.map(connection => <GitConnectionCard key={connection.id} connection={connection} canEdit={canEdit} refresh={refresh} />)}</div>
        {connections.data && !connections.error && (offset > 0 || connections.data.next_offset !== null) && <nav aria-label="Страницы репозиториев" className="flex gap-2"><Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Назад</Button><Button disabled={connections.data.next_offset === null} onClick={() => connections.data.next_offset !== null && setOffset(connections.data.next_offset)}>Дальше</Button></nav>}
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
