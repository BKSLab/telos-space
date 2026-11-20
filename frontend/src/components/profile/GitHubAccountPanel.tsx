import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Section } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { api } from "@/lib/api";
import { githubAccountKey, githubPath, githubRepositoriesKey, githubRetryAt, type GitHubAccountResponse } from "@/lib/github";
import { useGitHubPause } from "@/lib/useGitHubPause";
import { GitHubTokenGuide } from "./GitHubTokenGuide";

export function GitHubAccountPanel() {
    const client = useQueryClient();
    const account = useQuery({
        queryKey: githubAccountKey,
        queryFn: () => api.get<GitHubAccountResponse>(githubPath),
        retry: false,
    });
    const [token, setToken] = useState("");
    const [editing, setEditing] = useState(false);
    const [checkedAccount, setCheckedAccount] = useState<GitHubAccountResponse["account"]>(null);
    const [busy, setBusy] = useState<"check" | "save" | "delete" | null>(null);
    const pending = useRef(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [retryAt, setRetryAt] = useState<number | null>(null);
    const paused = useGitHubPause(retryAt);
    const savedAccount = account.data?.account;
    const showForm = !savedAccount || editing;

    function clearDraft() {
        setToken("");
        setCheckedAccount(null);
        setError("");
        setNotice("");
    }

    async function perform(action: "check" | "save" | "delete") {
        if (pending.current || paused) return;
        const value = token.trim();
        if (action !== "delete" && (!value || (action === "save" && !checkedAccount))) return;
        pending.current = true;
        setBusy(action);
        setError("");
        setNotice("");
        setRetryAt(null);
        try {
            if (action === "check") {
                setCheckedAccount(null);
                const result = await api.post<GitHubAccountResponse>(`${githubPath}/check`, { token: value });
                if (!result.account) throw new Error("Missing GitHub account");
                setCheckedAccount(result.account);
                return;
            }
            const result = action === "delete"
                ? (await api.delete(githubPath), { account: null })
                : await api.put<GitHubAccountResponse>(githubPath, { token: value });
            client.removeQueries({ queryKey: githubRepositoriesKey });
            client.setQueryData(githubAccountKey, result);
            clearDraft();
            setEditing(false);
            setConfirmDelete(false);
            if (action === "delete") setNotice("Токен удалён.");
        } catch (error) {
            setCheckedAccount(null);
            const deadline = githubRetryAt(error);
            if (deadline !== null) setRetryAt(deadline);
            else setError(action === "delete" ? "Не удалось удалить токен GitHub. Повторите попытку."
                : action === "check" ? "Не удалось проверить токен. Проверьте его значение и срок действия, затем повторите попытку."
                    : "Не удалось сохранить токен GitHub. Проверьте его ещё раз и повторите сохранение.");
        } finally {
            pending.current = false;
            setBusy(null);
        }
    }

    return <Section title="GitHub" className={showForm ? "pb-14 sm:pb-0" : undefined}>
        {account.isPending && <p role="status" className="text-sm text-muted">Загружаем настройки GitHub…</p>}
        {account.isError && <ErrorMessage message="Не удалось загрузить настройки GitHub." action={<Button onClick={() => void account.refetch()}>Повторить</Button>} />}
        {account.data && !account.isError && (savedAccount ? <div role="status" className="flex items-start gap-3 rounded-md border border-success/25 bg-success/10 px-4 py-3">
            <CheckCircle2 size={20} className="mt-0.5 shrink-0 text-success" aria-hidden="true" />
            <div className="min-w-0 space-y-1">
                <p className="text-sm font-medium text-success">Токен сохранён</p>
                <p className="break-words text-sm text-secondary">Аккаунт GitHub: {savedAccount.login}</p>
            </div>
        </div> : <p className="text-sm text-secondary">Аккаунт GitHub не подключён.</p>)}
        {busy && <p role="status" className="text-sm text-muted">{busy === "delete" ? "Удаляем токен…" : busy === "save" ? "Сохраняем токен…" : "Проверяем токен в GitHub…"}</p>}
        {notice && <p role="status" className="text-sm text-secondary">{notice}</p>}
        {error && <ErrorMessage title="Действие не выполнено" message={error} />}
        {retryAt !== null && <p role="status" className="text-sm text-warning">
            {paused ? <>GitHub ограничил запросы. Повтор доступен после <time dateTime={new Date(retryAt).toISOString()}>{new Date(retryAt).toLocaleString("ru-RU")}</time>.</> : "Можно повторить действие."}
        </p>}
        {account.data && !account.isError && (confirmDelete
            ? <div className="space-y-2">
                <p className="text-sm text-secondary">Удалить токен GitHub? Доступ к закрытым репозиториям может прерваться.</p>
                <div className="flex flex-wrap gap-2">
                    <Button variant="destructive" disabled={!!busy || paused} onClick={() => void perform("delete")}>Удалить токен</Button>
                    <Button disabled={!!busy} onClick={() => setConfirmDelete(false)}>Отмена</Button>
                </div>
            </div>
            : showForm ? <div className="space-y-3">
                {savedAccount && <p className="text-sm text-secondary">Новый токен заменит сохранённый после проверки и сохранения.</p>}
                <GitHubTokenGuide initiallyOpen={!savedAccount} />
                <form className="flex flex-col gap-3" onSubmit={event => { event.preventDefault(); void perform(checkedAccount ? "save" : "check"); }}>
                <Field label="Токен GitHub" hint="Используйте токен с доступом на чтение нужных репозиториев. Токен применяется при подключении источников к проектам.">
                    {id => <Input id={id} type="password" autoComplete="new-password" disabled={!!busy} value={token} onChange={event => {
                        setToken(event.target.value); setCheckedAccount(null); setError(""); setNotice("");
                    }} />}
                </Field>
                {checkedAccount && <div role="status" className="space-y-1 text-sm">
                    <p className="flex items-center gap-2 font-medium text-success"><CheckCircle2 size={18} aria-hidden="true" />Токен проверен</p>
                    <p className="break-words text-secondary">Аккаунт для подключения: {checkedAccount.login}. Нажмите «Сохранить токен».</p>
                    <p className="text-xs text-muted">Доступ к нужному репозиторию проверим при его подключении.</p>
                </div>}
                <div className="flex flex-wrap gap-2 max-sm:flex-col max-sm:items-start">
                    <Button type="submit" variant="primary" disabled={!!busy || paused || !token.trim()}>{checkedAccount ? "Сохранить токен" : "Проверить токен"}</Button>
                    {savedAccount && <Button disabled={!!busy} onClick={() => { clearDraft(); setEditing(false); }}>Отмена</Button>}
                </div>
                </form>
            </div> : <div className="flex flex-wrap gap-2">
                <Button onClick={() => { clearDraft(); setEditing(true); }}>Заменить токен</Button>
                <Button onClick={() => { clearDraft(); setConfirmDelete(true); }}>Удалить токен</Button>
            </div>)}
    </Section>;
}
