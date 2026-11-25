import { Check, CheckCircle2, Clock3, Files, GitCommitHorizontal, LoaderCircle, Pause, Search, ShieldAlert, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import { GIT_METADATA_REASONS, GIT_STATES, type GitConnection, type GitConnectionPage } from "@/lib/projectKnowledge";

function statusOf(connection: GitConnection) {
    if (!connection.enabled || connection.state === "disabled") return { label: "Отключён", tone: "muted", icon: Pause } as const;
    if (connection.last_error_code === "rate_limited") return { label: "Индексация приостановлена", tone: "warning", icon: Pause } as const;
    if (connection.last_error_code === "github_profile_pending") return { label: "Ожидает проверки доступа", tone: "warning", icon: Clock3 } as const;
    if (connection.state === "access_lost") return { label: "Доступ потерян", tone: "danger", icon: ShieldAlert } as const;
    if (connection.state === "error" || connection.last_error_code) return { label: "Ошибка индексации", tone: "danger", icon: TriangleAlert } as const;
    if (connection.coverage.truncated || connection.coverage.failed_files > 0) return { label: "Неполный охват", tone: "warning", icon: TriangleAlert } as const;
    if (connection.state === "pending") return { label: "В очереди", tone: "accent", icon: Clock3 } as const;
    if (connection.state === "syncing") return { label: "Индексируется", tone: "accent", icon: LoaderCircle } as const;
    if (connection.sync_pending) return { label: "В очереди на проверку", tone: "accent", icon: Clock3 } as const;
    if (connection.state === "partial") return { label: "Неполный охват", tone: "warning", icon: TriangleAlert } as const;
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
        <span className="font-medium text-secondary">{page.total === null ? `На странице: ${page.items.length}` : page.items.length < page.total ? `На странице: ${page.items.length} из ${page.total}` : `Источников: ${page.total}`}</span>
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

const syncReasons: Record<string, string> = { poll: "Плановая сверка", manual: "Ручной запуск", webhook: "Событие GitHub", task_stage: "Переход задачи", configuration: "Настройки подключения" };

export function GitIndexDetails({ connection }: { connection: GitConnection }) {
    const status = statusOf(connection);
    const disabled = !connection.enabled || connection.state === "disabled";
    const inaccessible = connection.state === "access_lost";
    const active = status.tone === "accent";
    const stale = Boolean(connection.observed_sha && connection.indexed_sha && connection.observed_sha !== connection.indexed_sha);
    const { total_files: total, cataloged_files: cataloged, indexed_files: indexed, metadata_files: metadata, failed_files: failed, truncated } = connection.coverage;
    const hasCoverage = total > 0 || cataloged > 0 || Boolean(connection.indexed_sha);
    const steps = [
        { icon: GitCommitHorizontal, title: "Версия Git", detail: disabled ? "Отключено" : inaccessible ? "Нет доступа" : connection.state === "pending" ? "Ждёт проверки" : connection.observed_sha ? "Обнаружена" : "Ещё не проверена", done: !disabled && !inaccessible && Boolean(connection.observed_sha) && connection.state !== "pending" },
        { icon: Files, title: "Индексация файлов", detail: disabled ? "Отключено" : active ? "Ожидает результата обновления" : status.tone === "success" ? "Завершена" : status.label, done: status.tone === "success" },
        { icon: Search, title: "Поиск Ариса", detail: disabled || inaccessible ? "Недоступен" : stale ? "Предыдущая версия" : status.tone === "success" ? metadata > 0 ? indexed > 0 ? "Текст и каталог файлов" : "Только каталог файлов" : "Актуальная версия" : "Проверяйте версию и охват", done: status.tone === "success" },
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
                    <p className="text-[13px] text-secondary"><strong className="text-lg font-semibold tabular-nums">{cataloged.toLocaleString("ru-RU")}</strong> из {total.toLocaleString("ru-RU")} файлов в каталоге</p>
                    {total > 0 && <div role="img" aria-label={`Охват файлов: текст ${indexed}, только метаданные ${metadata}, ошибок ${failed}, в каталоге ${cataloged}, всего ${total}`} className="flex h-2 overflow-hidden rounded-full border border-line-subtle bg-control">
                        <span className="shrink-0 bg-accent" style={{ width: `${Math.min(100, indexed / total * 100)}%` }} />
                        <span className="shrink-0 bg-muted/35" style={{ width: `${Math.min(100, metadata / total * 100)}%` }} />
                        <span className="shrink-0 bg-warning" style={{ width: `${Math.min(100, failed / total * 100)}%` }} />
                    </div>}
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted"><span className="flex items-center gap-1.5"><span aria-hidden="true" className="size-1.5 rounded-full bg-accent" />Текст: {indexed}</span><span>Только метаданные: {metadata}</span><span className={failed > 0 ? "text-warning" : undefined}>Ошибок: {failed}</span></div>
                    {metadata > 0 && <details className="text-[12px] text-muted">
                        <summary className="touch-target cursor-pointer text-accent focus-visible:outline-2 focus-visible:outline-accent">Почему только метаданные</summary>
                        <p className="mb-2">Эти объекты учтены в каталоге: доступны путь, версия и сведения о файле. Их содержимое не представлено текстом. Учёт в каталоге не означает, что Арис прочитал содержимое двоичного файла или внешнего объекта Git LFS/подмодуля.</p>
                        {Object.entries(connection.coverage.metadata_counts).some(([, count]) => count > 0) ? <dl className="space-y-1">{Object.entries(connection.coverage.metadata_counts).filter(([, count]) => count > 0).map(([reason, count]) => <div key={reason} className="flex justify-between gap-3"><dt>{GIT_METADATA_REASONS[reason] ?? "Другой формат содержимого"}</dt><dd className="tabular-nums">{count}</dd></div>)}</dl> : <p>Разбивка причин ещё не получена.</p>}
                    </details>}
                    {failed > 0 && <p className="text-[12px] text-warning">Часть файлов не удалось прочитать. Запись в каталоге сохраняется, но текст этих файлов пока недоступен.</p>}
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
                {connection.last_sync_reason && <div className="flex flex-wrap justify-between gap-x-2"><dt>Причина последнего запуска</dt><dd>{syncReasons[connection.last_sync_reason] ?? "Обновление источника"}</dd></div>}
                {connection.last_sync_requested_at && <div className="flex flex-wrap justify-between gap-x-2"><dt>Запрошено обновление</dt><dd><Stamp value={connection.last_sync_requested_at} /></dd></div>}
                {connection.poll_interval_seconds && <div className="flex flex-wrap justify-between gap-x-2"><dt>Резервная сверка</dt><dd>{connection.poll_interval_seconds === 3600 ? "Раз в час" : `Каждые ${connection.poll_interval_seconds} с`}</dd></div>}
            </dl>
        </div>
        <dl aria-label="Доставка событий GitHub" className="grid gap-x-5 gap-y-2 border-t border-line-subtle pt-3 text-[12px] text-muted sm:grid-cols-2">
            <div><dt className="mb-1 text-secondary">Webhook GitHub</dt><dd>{!connection.has_webhook_secret ? "Секрет не настроен" : disabled ? "Приём приостановлен: источник отключён" : "Секрет сохранён в TELOS"}</dd></div>
            <div><dt className="mb-1 text-secondary">Последняя доставка</dt><dd>{connection.last_webhook_received_at ? <><Stamp value={connection.last_webhook_received_at} />{connection.last_webhook_event && <span className="ml-1">· {connection.last_webhook_event === "ping" ? "Проверка связи (ping)" : connection.last_webhook_event}</span>}</> : "Событий ещё не получали"}</dd></div>
            {connection.last_webhook_processed_at ? <div className="sm:col-span-2"><dt className="mb-1 text-secondary">Последняя успешная сверка события</dt><dd><Stamp value={connection.last_webhook_processed_at} /> · Готовность кода проверяйте по версии и охвату индекса.</dd></div> : connection.has_webhook_secret && <div className="sm:col-span-2"><dt className="mb-1 text-secondary">Сверка события</dt><dd>Успешная сверка события ещё не подтверждена. Получение ping не подтверждает индексацию.</dd></div>}
        </dl>
    </div>;
}
