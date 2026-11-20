import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Button, LinkButton } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { api, ApiError } from "@/lib/api";
import {
    createRepositoryDraft, githubAccountKey, githubPath, githubRepositoriesKey, githubRetryAt,
    repositoryFromUrl, repositoryKey, type GitHubAccountResponse, type GitHubRepositoryPage, type RepositoryDraft,
} from "@/lib/github";
import { useGitHubPause } from "@/lib/useGitHubPause";

export function GitHubRepositoryPicker({ value, onChange, disabled }: {
    value: RepositoryDraft[];
    onChange: (value: RepositoryDraft[]) => void;
    disabled?: boolean;
}) {
    const [search, setSearch] = useState("");
    const [url, setUrl] = useState("");
    const [urlError, setUrlError] = useState("");
    const account = useQuery({
        queryKey: githubAccountKey,
        queryFn: () => api.get<GitHubAccountResponse>(githubPath),
        retry: false,
    });
    const repositories = useInfiniteQuery({
        queryKey: [...githubRepositoriesKey, account.data?.account?.id],
        enabled: Boolean(account.data?.account),
        initialPageParam: 1,
        queryFn: ({ pageParam }) => api.get<GitHubRepositoryPage>(`${githubPath}/repositories?page=${pageParam}`),
        getNextPageParam: last => last.next_page ?? undefined,
        retry: false,
        retryOnMount: false,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        staleTime: Infinity,
    });
    const retryAt = githubRetryAt(repositories.error, repositories.errorUpdatedAt);
    const paused = useGitHubPause(retryAt);
    const catalog = new Map(repositories.data?.pages.flatMap(page => page.repositories.map(repo => [
        repositoryKey({ owner: repo.owner, repo: repo.name }), repo,
    ] as const)));
    const matches = [...catalog].filter(([key]) => key.includes(search.trim().toLowerCase()));
    const selected = new Set(value.map(repositoryKey));
    const needsProfile = account.data?.account === null || (repositories.error instanceof ApiError && repositories.error.status === 409);

    function addUrl() {
        const parsed = repositoryFromUrl(url);
        if (!parsed) {
            setUrlError("Введите ссылку вида https://github.com/owner/repository.");
            return;
        }
        const key = repositoryKey(parsed);
        if (selected.has(key)) {
            setUrlError("Репозиторий уже выбран.");
            return;
        }
        onChange([...value, createRepositoryDraft(parsed.owner, parsed.repo, catalog.get(key))]);
        setUrl("");
        setUrlError("");
    }

    if (account.isPending) return <p role="status" className="text-sm text-muted">Проверяем подключение к GitHub…</p>;
    if (account.isError) return <ErrorMessage message="Не удалось проверить подключение к GitHub." action={<Button onClick={() => void account.refetch()}>Повторить</Button>} />;
    if (needsProfile) return <LinkButton to="/profile?tab=github">Настроить доступ к GitHub</LinkButton>;

    return <div className="min-w-0 space-y-4">
        <div className="space-y-2">
            <Field label="Поиск репозиториев" hint={repositories.hasNextPage ? "Поиск по загруженным страницам. Можно загрузить ещё." : undefined}>
                {id => <Input id={id} type="search" placeholder="Владелец или название" value={search} onChange={event => setSearch(event.target.value)} />}
            </Field>
            {repositories.isFetching && <p role="status" className="text-sm text-muted">Загружаем репозитории…</p>}
            {matches.length > 0 && <ul className="max-h-56 overflow-auto rounded-[var(--radius-control)] border border-line" aria-label="Репозитории GitHub">
                {matches.map(([key, repo]) => <li key={key} className="border-b border-line-subtle last:border-b-0">
                    <label className="touch-target flex items-center gap-2 px-3 py-1 text-sm text-secondary hover:bg-hover">
                        <input type="checkbox" checked={selected.has(key)} disabled={disabled} onChange={event => onChange(event.target.checked
                            ? [...value, createRepositoryDraft(repo.owner, repo.name, repo)]
                            : value.filter(item => repositoryKey(item) !== key))} />
                        <span className="min-w-0 break-all">{repo.owner}/{repo.name}{repo.private && <span className="ml-2 text-xs text-muted">Закрытый</span>}</span>
                    </label>
                </li>)}
            </ul>}
            {!repositories.isFetching && !repositories.isError && matches.length === 0 && <p className="text-sm text-muted">На загруженных страницах репозитории не найдены.</p>}
            {repositories.isError && (retryAt !== null
                ? <p role="status" className="text-sm text-warning">{paused ? <>GitHub ограничил запросы. Повтор доступен после <time dateTime={new Date(retryAt).toISOString()}>{new Date(retryAt).toLocaleTimeString("ru-RU")}</time>.</> : "Можно снова загрузить репозитории."}</p>
                : <ErrorMessage message="Не удалось загрузить репозитории GitHub." />)}
            {(repositories.hasNextPage || repositories.isError) && <Button disabled={disabled || paused || repositories.isFetching} onClick={() => {
                if (repositories.data && repositories.hasNextPage) void repositories.fetchNextPage();
                else void repositories.refetch();
            }}>{repositories.isError ? "Повторить загрузку" : "Загрузить ещё"}</Button>}
        </div>
        <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-end">
            <div className="w-full flex-1">
                <Field label="Ссылка на репозиторий GitHub" error={urlError || undefined}>
                    {id => <Input id={id} type="url" placeholder="https://github.com/owner/repository" value={url} disabled={disabled} onChange={event => { setUrl(event.target.value); setUrlError(""); }} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); addUrl(); } }} />}
                </Field>
            </div>
            <Button disabled={disabled || !url.trim()} onClick={addUrl}>Добавить по ссылке</Button>
        </div>
        {value.length > 0 && <ul aria-label="Выбранные репозитории" className="space-y-3">
            {value.map(draft => {
                const key = repositoryKey(draft);
                const update = (changes: Partial<RepositoryDraft>) => onChange(value.map(item => repositoryKey(item) === key ? { ...item, ...changes } : item));
                return <li key={key} className="space-y-3 rounded-[var(--radius-control)] border border-line p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="min-w-0 break-all text-sm font-medium text-primary">{draft.owner}/{draft.repo}</span>
                        <Button size="sm" disabled={disabled} onClick={() => onChange(value.filter(item => repositoryKey(item) !== key))} aria-label={`Убрать ${draft.owner}/${draft.repo}`}>Убрать</Button>
                    </div>
                    <Field label="Ветка или ref" hint={draft.defaultBranch ? `По умолчанию GitHub выберет ветку ${draft.defaultBranch}.` : "Пустое поле выбирает основную ветку репозитория."}>
                        {id => <Input id={id} value={draft.ref} disabled={disabled} maxLength={1024} placeholder={draft.defaultBranch} onChange={event => update({ ref: event.target.value })} />}
                    </Field>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Название источника">{id => <Input id={id} value={draft.name} maxLength={160} disabled={disabled} onChange={event => update({ name: event.target.value })} />}</Field>
                        <Field label="Назначение">{id => <Input id={id} value={draft.purpose} maxLength={1000} disabled={disabled} onChange={event => update({ purpose: event.target.value })} />}</Field>
                    </div>
                    {draft.private !== false && <label className="touch-target flex items-center gap-2 text-sm text-secondary">
                        <input type="checkbox" checked={draft.share_private_with_project} disabled={disabled} onChange={event => update({ share_private_with_project: event.target.checked })} />
                        {draft.private ? "Разрешаю команде проекта читать закрытый репозиторий (обязательно)" : "Если репозиторий закрытый, разрешаю команде проекта читать его"}
                    </label>}
                </li>;
            })}
        </ul>}
    </div>;
}
