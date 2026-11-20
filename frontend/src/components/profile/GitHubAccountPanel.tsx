import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/Button";
import { Section } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { api } from "@/lib/api";
import { githubAccountKey, githubPath, githubRepositoriesKey, githubRetryAt, type GitHubAccountResponse } from "@/lib/github";
import { useGitHubPause } from "@/lib/useGitHubPause";

export function GitHubAccountPanel() {
    const client = useQueryClient();
    const account = useQuery({
        queryKey: githubAccountKey,
        queryFn: () => api.get<GitHubAccountResponse>(githubPath),
        retry: false,
    });
    const [token, setToken] = useState("");
    const [busy, setBusy] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [retryAt, setRetryAt] = useState<number | null>(null);
    const paused = useGitHubPause(retryAt);

    async function update(remove: boolean) {
        setBusy(true);
        setError("");
        setNotice("");
        setRetryAt(null);
        const value = token.trim();
        setToken("");
        try {
            const result = remove
                ? (await api.delete(githubPath), { account: null })
                : await api.put<GitHubAccountResponse>(githubPath, { token: value });
            client.removeQueries({ queryKey: githubRepositoriesKey });
            client.setQueryData(githubAccountKey, result);
            setConfirmDelete(false);
            setNotice(remove ? "Токен удалён." : "Подключение сохранено.");
        } catch (error) {
            const deadline = githubRetryAt(error);
            if (deadline !== null) setRetryAt(deadline);
            else setError(remove ? "Не удалось удалить токен GitHub. Повторите попытку." : "Не удалось сохранить токен GitHub. Проверьте токен и его разрешения.");
        } finally {
            setBusy(false);
        }
    }

    return <Section title="GitHub">
        {account.isPending && <p role="status" className="text-sm text-muted">Проверяем подключение к GitHub…</p>}
        {account.isError && <ErrorMessage message="Не удалось проверить подключение к GitHub." action={<Button onClick={() => void account.refetch()}>Повторить</Button>} />}
        {account.data && <p className="break-words text-sm text-secondary">{account.data.account ? `Аккаунт GitHub: ${account.data.account.login}` : "Аккаунт GitHub не подключён."}</p>}
        {busy && <p role="status" className="text-sm text-muted">{confirmDelete ? "Удаляем токен…" : "Проверяем токен…"}</p>}
        {notice && <p role="status" className="text-sm text-secondary">{notice}</p>}
        {error && <ErrorMessage message={error} />}
        {retryAt !== null && <p role="status" className="text-sm text-warning">
            {paused ? <>GitHub ограничил запросы. Повтор доступен после <time dateTime={new Date(retryAt).toISOString()}>{new Date(retryAt).toLocaleString("ru-RU")}</time>.</> : "Можно повторить действие."}
        </p>}
        {account.data && (confirmDelete
            ? <div className="space-y-2">
                <p className="text-sm text-secondary">Удалить токен GitHub? Доступ к закрытым репозиториям может прерваться.</p>
                <div className="flex flex-wrap gap-2">
                    <Button variant="destructive" disabled={busy || paused} onClick={() => void update(true)}>Удалить токен</Button>
                    <Button disabled={busy} onClick={() => setConfirmDelete(false)}>Отмена</Button>
                </div>
            </div>
            : <form className="flex flex-col gap-3" onSubmit={event => { event.preventDefault(); if (token.trim() && !busy && !paused) void update(false); }}>
                <Field label="Токен GitHub" hint="Используйте токен с доступом на чтение нужных репозиториев. Токен применяется при подключении источников к проектам.">
                    {id => <Input id={id} type="password" autoComplete="new-password" disabled={busy} value={token} onChange={event => setToken(event.target.value)} />}
                </Field>
                <div className="flex flex-wrap gap-2">
                    <Button type="submit" variant="primary" disabled={busy || paused || !token.trim()}>{account.data.account ? "Заменить токен" : "Подключить GitHub"}</Button>
                    {account.data.account && <Button disabled={busy} onClick={() => { setToken(""); setConfirmDelete(true); }}>Удалить токен</Button>}
                </div>
            </form>)}
    </Section>;
}
