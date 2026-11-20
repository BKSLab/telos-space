import { Check, CheckCircle2, Clock3, Files, GitCommitHorizontal, LoaderCircle, Pause, Search, ShieldAlert, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import { GIT_STATES, type GitConnection, type GitConnectionPage } from "@/lib/projectKnowledge";

function statusOf(connection: GitConnection) {
    if (!connection.enabled || connection.state === "disabled") return { label: "Отключён", tone: "muted", icon: Pause } as const;
    if (connection.last_error_code === "rate_limited") return { label: "Индексация приостановлена", tone: "warning", icon: Pause } as const;
    if (connection.last_error_code === "github_profile_pending") return { label: "Ожидает проверки доступа", tone: "warning", icon: Clock3 } as const;
    if (connection.state === "access_lost") return { label: "Доступ потерян", tone: "danger", icon: ShieldAlert } as const;
    if (connection.state === "error" || connection.last_error_code) return { label: "Ошибка индексации", tone: "danger", icon: TriangleAlert } as const;
    if (connection.state === "pending") return { label: "В очереди", tone: "accent", icon: Clock3 } as const;
    if (connection.state === "syncing") return { label: "Индексируется", tone: "accent", icon: LoaderCircle } as const;
    if (connection.state === "partial" || connection.coverage.truncated || connection.coverage.failed_files > 0 || connection.coverage.excluded_files > 0) return { label: "Неполный охват", tone: "warning", icon: TriangleAlert } as const;
    if (connection.observed_sha && connection.indexed_sha && connection.observed_sha !== connection.indexed_sha) return { label: "Есть новая версия", tone: "warning", icon: GitCommitHorizontal } as const;
    if (connection.state === "ready" && connection.indexed_sha && connection.indexed_sha === connection.observed_sha) return { label: "Индекс актуален", tone: "success", icon: CheckCircle2 } as const;
    return { label: GIT_STATES[connection.state] === "Готов" ? "Версия не подтверждена" : GIT_STATES[connection.state] ?? "Состояние уточняется", tone: "muted", icon: Clock3 } as const;
}

const tones = {
    success: "border-success/25 bg-success/10 text-success",
    warning: "border-warning/25 bg-warning/10 text-warning",
    danger: "border-danger/25 bg-danger/10 text-danger",
    accent: "border-accent-border bg-accent-soft text-accent",
    muted: "border-line-subtle bg-control text-muted",
};

export function GitIndexBadge({ connection }: { connection: GitConnection }) {
    const status = statusOf(connection);
    const Icon = status.icon;
    return <Badge className={cn("max-w-full self-start px-2 py-1 text-[12px]", tones[status.tone])}>
        <Icon size={14} aria-hidden="true" className={cn("shrink-0", connection.state === "syncing" && status.tone === "accent" && "motion-safe:animate-spin")} />
        {status.label}
    </Badge>;
}

export function GitIndexSummary({ page }: { page: GitConnectionPage }) {
    if (page.total === 0) return null;
    const counts = { success: 0, accent: 0, warning: 0, danger: 0, muted: 0 };
    page.items.forEach(connection => { counts[statusOf(connection).tone]++; });
    return <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-line-subtle pb-3 text-[12px]" aria-label="Сводка индексации">
        <span className="font-medium text-secondary">{page.items.length < page.total ? `На странице: ${page.items.length} из ${page.total}` : `Источников: ${page.total}`}</span>
        {counts.success > 0 && <span className="flex items-center gap-1.5 text-success"><CheckCircle2 size={14} aria-hidden="true" />Актуальны: {counts.success}</span>}
        {counts.accent > 0 && <span className="flex items-center gap-1.5 text-accent"><Clock3 size={14} aria-hidden="true" />В работе: {counts.accent}</span>}
        {counts.warning + counts.danger > 0 && <span className="flex items-center gap-1.5 text-warning"><TriangleAlert size={14} aria-hidden="true" />Требуют внимания: {counts.warning + counts.danger}</span>}
        {counts.muted > 0 && <span className="text-muted">Остальные: {counts.muted}</span>}
    </div>;
}

function Commit({ sha, empty }: { sha: string | null; empty: string }) {
    return sha ? <code tabIndex={0} title={sha} aria-label={sha} className="rounded border border-line-subtle bg-control px-1.5 py-0.5 font-mono text-[12px] text-secondary focus-visible:outline-2 focus-visible:outline-accent">{sha.slice(0, 8)}</code> : <span>{empty}</span>;
}

function Stamp({ value }: { value: string }) {
    return <time dateTime={value}>{new Date(value).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", year: "numeric" })}</time>;
}

export function GitIndexDetails({ connection }: { connection: GitConnection }) {
    const status = statusOf(connection);
    const disabled = !connection.enabled || connection.state === "disabled";
    const inaccessible = connection.state === "access_lost";
    const active = status.tone === "accent";
    const stale = Boolean(connection.observed_sha && connection.indexed_sha && connection.observed_sha !== connection.indexed_sha);
    const { total_files: total, indexed_files: indexed, excluded_files: excluded, failed_files: failed, truncated } = connection.coverage;
    const hasCoverage = total > 0 || indexed > 0 || excluded > 0 || failed > 0 || Boolean(connection.indexed_sha);
    const steps = [
        { icon: GitCommitHorizontal, title: "Версия Git", detail: disabled ? "Отключено" : inaccessible ? "Нет доступа" : connection.state === "pending" ? "Ждёт проверки" : connection.observed_sha ? "Обнаружена" : "Ещё не проверена", done: !disabled && !inaccessible && Boolean(connection.observed_sha) && connection.state !== "pending" },
        { icon: Files, title: "Индексация файлов", detail: disabled ? "Отключено" : active ? "Ожидает результата обновления" : status.tone === "success" ? "Завершена" : status.label, done: status.tone === "success" },
        { icon: Search, title: "Поиск Ариса", detail: disabled || inaccessible ? "Недоступен" : stale ? "Предыдущая версия" : status.tone === "success" ? "Актуальная версия" : "Проверяйте версию и охват", done: status.tone === "success" },
    ];
    return <div className="space-y-3">
        <ol aria-label="Этапы индексации" className="grid grid-cols-1 gap-2 rounded-lg border border-line-subtle bg-control/50 p-3 sm:grid-cols-3 sm:gap-3">
            {steps.map(({ icon: Icon, title, detail, done }) => <li key={title} className="flex min-w-0 items-center gap-2.5">
                <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-full border", done ? tones.success : tones.muted)}>
                    {done ? <Check size={14} aria-hidden="true" /> : <Icon size={14} aria-hidden="true" />}
                </span>
                <div className="min-w-0"><p className="text-[12px] font-medium text-secondary">{title}</p><p className="text-[11px] text-muted">{detail}</p></div>
            </li>)}
        </ol>
        {stale && !disabled && !inaccessible && <p className="flex items-start gap-2 text-[12px] text-warning"><GitCommitHorizontal size={15} className="mt-0.5 shrink-0" aria-hidden="true" />В Git есть новая версия. Индекс ещё не догнал её.</p>}
        <div className="grid min-w-0 gap-3 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] lg:gap-5">
            <div className="min-w-0 space-y-2">
                <div className="flex flex-wrap items-baseline justify-between gap-1 text-[12px]"><span className="font-medium text-secondary">Охват файлов</span><span className="text-muted">Последние данные индексации</span></div>
                {hasCoverage ? <>
                    <p className="text-[13px] text-secondary"><strong className="text-lg font-semibold tabular-nums">{indexed.toLocaleString("ru-RU")}</strong> из {total.toLocaleString("ru-RU")} файлов в индексе</p>
                    {total > 0 && <div role="img" aria-label={`Охват файлов: в индексе ${indexed}, исключено ${excluded}, ошибок ${failed}, всего ${total}`} className="flex h-2 overflow-hidden rounded-full border border-line-subtle bg-control">
                        <span className="shrink-0 bg-accent" style={{ width: `${Math.min(100, indexed / total * 100)}%` }} />
                        <span className="shrink-0 bg-muted/35" style={{ width: `${Math.min(100, excluded / total * 100)}%` }} />
                        <span className="shrink-0 bg-warning" style={{ width: `${Math.min(100, failed / total * 100)}%` }} />
                    </div>}
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted"><span className="flex items-center gap-1.5"><span aria-hidden="true" className="size-1.5 rounded-full bg-accent" />В индексе: {indexed}</span><span>Исключено: {excluded}</span><span className={failed > 0 ? "text-warning" : undefined}>Ошибок: {failed}</span></div>
                    {active && <p className="text-[11px] text-muted">Это охват файлов, а не процент выполнения текущего обновления.</p>}
                </> : <p className="py-1 text-[12px] text-muted">Данные об охвате появятся после обработки файлов.</p>}
                {truncated && <p className="text-[12px] text-warning">Охват ограничен. Данные не отражают весь репозиторий.</p>}
            </div>
            <dl className="grid content-start gap-2 border-t border-line-subtle pt-3 text-[12px] text-muted lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
                <div className="flex flex-wrap items-center justify-between gap-2"><dt>Обнаруженная версия</dt><dd><Commit sha={connection.observed_sha} empty="Не проверена" /></dd></div>
                <div className="flex flex-wrap items-center justify-between gap-2"><dt>Проиндексированная версия</dt><dd><Commit sha={connection.indexed_sha} empty="Ещё не опубликована" /></dd></div>
                {connection.indexed_at && <div className="flex flex-wrap justify-between gap-x-2"><dt>Индекс обновлён</dt><dd><Stamp value={connection.indexed_at} /></dd></div>}
                {connection.checked_at && <div className="flex flex-wrap justify-between gap-x-2"><dt>Git проверен</dt><dd><Stamp value={connection.checked_at} /></dd></div>}
                {connection.next_poll_at && !disabled && <div className="flex flex-wrap justify-between gap-x-2"><dt>Следующая проверка</dt><dd><Stamp value={connection.next_poll_at} /></dd></div>}
            </dl>
        </div>
    </div>;
}
