import { useDeferredValue, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Plus, ShieldAlert } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { fullName, type ProjectMember, type Task } from "@/lib/types";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { useRiskSummary } from "@/lib/useRisks";
import { RISK_LEVEL_LABELS, RISK_STATUS_LABELS, riskQuery, type RiskPage, type RiskRating } from "@/lib/risks";
import { useUiStore } from "@/stores/ui";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { ResponsiveFilters } from "@/components/ui/ResponsiveFilters";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { CreateRiskDialog } from "@/components/risks/CreateRiskDialog";
import { RiskMatrix } from "@/components/risks/RiskMatrix";
import { RiskRegister } from "@/components/risks/RiskRegister";
import { RiskSuggestions } from "@/components/risks/RiskSuggestions";

const PAGE_SIZE = 25;

export function ProjectRisksPage() {
    const project = useProjectOutlet();
    const [params] = useSearchParams();
    return <ProjectRisksContent key={`${project.id}:${params.toString()}`} projectId={project.id} initialParams={params} />;
}

function ProjectRisksContent({ projectId, initialParams }: { projectId: number; initialParams: URLSearchParams }) {
    const [creating, setCreating] = useState(false);
    const [search, setSearch] = useState("");
    const [status, setStatus] = useState(initialParams.get("status") === "all" ? "" : "active");
    const [level, setLevel] = useState(initialParams.get("risk_level") ?? "");
    const [taskFilter, setTaskFilter] = useState(initialParams.get("task_id") ?? "");
    const [owner, setOwner] = useState("");
    const [probability, setProbability] = useState<RiskRating | null>(null);
    const [impact, setImpact] = useState<RiskRating | null>(null);
    const [page, setPage] = useState(1);
    const deferredSearch = useDeferredValue(search.trim());
    const openRisk = useUiStore((state) => state.setSelectedRisk);
    useEffect(() => {
        const riskId = Number(initialParams.get("risk"));
        if (Number.isInteger(riskId) && riskId > 0) openRisk({ projectId, riskId });
    }, [initialParams, openRisk, projectId]);
    const baseQuery = riskQuery({
        search: deferredSearch, status: status !== "active" ? status : "",
        active_only: status === "active", risk_level: level, owner_user_id: owner,
        task_id: taskFilter,
    });
    const query = `${baseQuery}&${riskQuery({ probability, impact, page, page_size: PAGE_SIZE })}`;
    const list = useQuery({
        queryKey: queryKeys.projectRiskList(projectId, query),
        queryFn: () => api.get<RiskPage>(`${endpoints.projectRisks(projectId)}?${query}`),
    });
    const summary = useRiskSummary(projectId, baseQuery);
    const members = useQuery({
        queryKey: queryKeys.projectMembers(projectId),
        queryFn: () => api.get<ProjectMember[]>(endpoints.projectMembers(projectId)),
    });
    const tasks = useQuery({
        queryKey: queryKeys.tasks(projectId),
        queryFn: () => api.get<Task[]>(endpoints.projectTasks(projectId)),
    });
    const reset = () => {
        setSearch(""); setStatus("active"); setLevel(""); setOwner("");
        setTaskFilter("");
        setProbability(null); setImpact(null); setPage(1);
    };
    const filtered = !!search || status !== "active" || !!level || !!owner || !!probability || !!taskFilter;
    const pages = Math.max(1, Math.ceil((list.data?.total ?? 0) / PAGE_SIZE));

    return (
        <div className="scrollbar-thin h-full overflow-y-auto">
            <div className="mx-auto flex w-full min-w-0 max-w-none flex-col gap-5 px-3 py-4 pb-24 sm:px-5 sm:py-5">
                <header className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h1 className="text-xl font-semibold tracking-tight text-primary">Риски</h1>
                        <p className="mt-1 text-[12px] text-muted">Что может помешать проекту и как мы с этим работаем</p>
                    </div>
                    <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>Добавить риск</Button>
                </header>
                <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[13px]" aria-live="polite">
                    {summary.isPending ? <span className="text-muted">Загрузка сводки…</span> : summary.data && <>
                        <span className="text-secondary"><strong className="font-mono text-primary">{summary.data.active_risks}</strong> активных</span>
                        <span className={summary.data.high_risks ? "text-danger" : "text-muted"}><strong className="font-mono">{summary.data.high_risks}</strong> HIGH</span>
                        <span className={summary.data.risks_due_for_review ? "text-warning" : "text-muted"}><strong className="font-mono">{summary.data.risks_due_for_review}</strong> требуют контроля</span>
                        {summary.data.occurred_risks > 0 && <span className="text-danger">{summary.data.occurred_risks} реализовались</span>}
                    </>}
                </div>
                <RiskSuggestions projectId={projectId} />
                <ResponsiveFilters active={filtered}>
                <div className="grid gap-3 border-y border-line-subtle py-4 sm:grid-cols-2 xl:grid-cols-[2fr_1fr_1fr_1fr_auto]">
                    <Field label="Поиск рисков">{(id) => <Input id={id} type="search" maxLength={255} placeholder="Название, описание или RISK-12" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />}</Field>
                    <Field label="Статус">{(id) => <Select id={id} value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
                        <option value="active">Все активные</option><option value="">Все статусы</option>
                        {Object.entries(RISK_STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </Select>}</Field>
                    <Field label="Уровень">{(id) => <Select id={id} value={level} onChange={(e) => { setLevel(e.target.value); setPage(1); }}>
                        <option value="">Любой уровень</option>{Object.entries(RISK_LEVEL_LABELS).map(([value, label]) => <option key={value} value={value}>{label} · {value}</option>)}
                    </Select>}</Field>
                    <Field label="Ответственный">{(id) => <Select id={id} value={owner} disabled={members.isPending || !!members.error} onChange={(e) => { setOwner(e.target.value); setPage(1); }}>
                        <option value="">Все участники</option>{members.data?.map((member) => <option key={member.user.id} value={member.user.id}>{fullName(member.user)}</option>)}
                    </Select>}</Field>
                    {filtered && <Button className="self-end" variant="ghost" onClick={reset}>Сбросить</Button>}
                </div>
                {members.error && <p role="alert" className="text-[12px] text-warning">Фильтр по ответственному недоступен. <button type="button" className="underline" onClick={() => void members.refetch()}>Повторить загрузку команды</button></p>}
                <Field label="Связанная задача" className="max-w-sm">{(id) => <Select id={id} value={taskFilter} onChange={(e) => { setTaskFilter(e.target.value); setPage(1); }}><option value="">Все задачи</option>{tasks.data?.map((task) => <option key={task.id} value={task.id}>{task.key} · {task.title}</option>)}</Select>}</Field>
                </ResponsiveFilters>
                <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
                    <section aria-labelledby="risk-register-title" className="min-w-0">
                        <div className="mb-2 flex items-center justify-between px-3">
                            <h2 id="risk-register-title" className="text-[11px] font-semibold tracking-[0.09em] text-muted uppercase">Реестр рисков</h2>
                            {list.data && <span className="font-mono text-[11px] text-muted">{list.data.total}</span>}
                        </div>
                        {list.isPending && <div role="status" aria-label="Загрузка реестра рисков" className="space-y-3"><Skeleton className="h-28" /><Skeleton className="h-28" /><Skeleton className="h-28" /></div>}
                        {list.error && <ErrorMessage message={list.error.message} action={<Button onClick={() => void list.refetch()}>Повторить</Button>} />}
                        {list.data && list.data.items.length === 0 && <EmptyState
                            icon={<ShieldAlert size={24} />}
                            title={filtered || page > 1 ? "Риски не найдены" : "В проекте пока нет активных рисков"}
                            description={filtered || page > 1 ? "Измените фильтры или вернитесь к началу реестра." : "Зафиксируйте то, что может повлиять на сроки, результат или выполнение проекта."}
                            action={<Button onClick={filtered || page > 1 ? reset : () => setCreating(true)}>{filtered || page > 1 ? "Сбросить фильтры" : "Добавить первый риск"}</Button>}
                        />}
                        {list.data && list.data.items.length > 0 && <RiskRegister risks={list.data.items} members={members.data ?? []} tasks={tasks.data ?? []} onOpen={(riskId) => openRisk({ projectId, riskId })} />}
                        {(pages > 1 || page > 1) && <nav aria-label="Страницы реестра" className="mt-4 flex items-center justify-between border-t border-line-subtle pt-3">
                            <Button aria-label="Предыдущая страница рисков" disabled={page <= 1 || list.isPending} onClick={() => setPage((value) => value - 1)} icon={<ChevronLeft size={14} />}>Назад</Button>
                            <span className="text-[11px] text-muted">{page} / {pages}</span>
                            <Button aria-label="Следующая страница рисков" disabled={page >= pages || list.isPending} onClick={() => setPage((value) => value + 1)} icon={<ChevronRight size={14} />}>Далее</Button>
                        </nav>}
                    </section>
                    <div className="lg:sticky lg:top-5">
                        {summary.error ? <ErrorMessage title="Матрица недоступна" message={summary.error.message} action={<Button onClick={() => void summary.refetch()}>Повторить</Button>} /> :
                            summary.isPending ? <div role="status" aria-label="Загрузка матрицы"><Skeleton className="h-72" /></div> :
                            <RiskMatrix cells={summary.data?.matrix ?? []} probability={probability} impact={impact} onSelect={(p, i) => { setProbability(p); setImpact(i); setPage(1); }} />}
                    </div>
                </div>
            </div>
            {creating && <CreateRiskDialog projectId={projectId} onClose={() => setCreating(false)} onCreated={(risk) => { setPage(1); openRisk({ projectId, riskId: risk.id }); }} />}
        </div>
    );
}
