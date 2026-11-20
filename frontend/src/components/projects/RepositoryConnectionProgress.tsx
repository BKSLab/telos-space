import { Button } from "@/components/ui/Button";
import { repositoryKey } from "@/lib/github";
import { useGitHubPause } from "@/lib/useGitHubPause";
import type { ConnectionAttempt } from "@/lib/useRepositoryConnections";

const labels = {
    waiting: "Ожидает подключения",
    connecting: "Подключаем…",
    connected: "Подключён",
    failed: "Ошибка подключения",
};

export function RepositoryConnectionProgress({ items, retryAt, pending, onRetry }: {
    items: ConnectionAttempt[];
    retryAt: number | null;
    pending: boolean;
    onRetry: () => void;
}) {
    const paused = useGitHubPause(retryAt);
    const connected = items.filter(item => item.state === "connected").length;
    if (!items.length) return null;
    return <div className="space-y-3">
        <p role="status" className="text-sm text-secondary">Подключено {connected} из {items.length}.</p>
        <ul className="space-y-1 text-sm text-secondary">
            {items.map(({ draft, state, error }) => <li key={repositoryKey(draft)} className="break-words">
                {draft.owner}/{draft.repo} · {labels[state]}
                {error && <p className="text-warning">{error}</p>}
            </li>)}
        </ul>
        {paused && retryAt !== null && <p role="status" className="text-sm text-warning">
            GitHub ограничил запросы. Повтор доступен после <time dateTime={new Date(retryAt).toISOString()}>{new Date(retryAt).toLocaleString("ru-RU")}</time>.
        </p>}
        {connected < items.length && <Button disabled={pending || paused} onClick={onRetry}>Повторить оставшиеся подключения</Button>}
    </div>;
}
