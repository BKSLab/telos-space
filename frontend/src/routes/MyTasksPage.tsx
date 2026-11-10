import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ListChecks, Plus } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { filterMyTasks, isClosedStatus, MY_ROLE_LABELS, MY_TASK_ROLES } from "@/lib/tasks";
import { executorOf } from "@/lib/taskParticipants";
import { fullName, type MyTask, type MyTaskRoleFilter, type Project } from "@/lib/types";
import { useUiStore } from "@/stores/ui";
import { useTaskAttention } from "@/lib/useTaskAttention";
import { Page } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { PriorityBadge } from "@/components/ui/Badge";
import { DueDate } from "@/components/ui/DueDate";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { CreatePersonalTaskDialog } from "@/components/tasks/CreatePersonalTaskDialog";
import { TaskSourceLabel } from "@/components/tasks/TaskSourceLabel";
import { MyTaskProjectFilter } from "@/components/tasks/MyTaskProjectFilter";

const PAGE_SIZE = 50;
export function MyTasksPage() {
    const [searchParams, setSearchParams] = useSearchParams();
    const signal = ["new", "overdue", "updated", "unread"].includes(searchParams.get("attention") ?? "") ? searchParams.get("attention")! : "all";
    const [role, setRole] = useState<MyTaskRoleFilter>("all");
    const [projectId, setProjectId] = useState<number>();
    const [includeClosed, setIncludeClosed] = useState(() => signal !== "all");
    const [search, setSearch] = useState("");
    const [page, setPage] = useState(0);
    const [creating, setCreating] = useState(false);
    const selectedTaskId = useUiStore(state => state.selectedTaskId);
    const setSelectedTaskId = useUiStore(state => state.setSelectedTaskId);
    const linkedTaskId = Number(searchParams.get("task"));
    useEffect(() => {
        if (!Number.isInteger(linkedTaskId) || linkedTaskId <= 0) return;
        setSelectedTaskId(linkedTaskId);
        setSearchParams(current => { const next = new URLSearchParams(current); next.delete("task"); return next; }, { replace: true });
    }, [linkedTaskId, setSearchParams, setSelectedTaskId]);
    const tasks = useQuery({ queryKey: queryKeys.myTaskList(role, includeClosed, signal === "all" ? undefined : signal, projectId), queryFn: () => api.get<MyTask[]>(endpoints.myTasks(role, includeClosed, signal === "all" ? undefined : signal, projectId)) });
    const projects = useQuery({ queryKey: queryKeys.projects, queryFn: () => api.get<Project[]>(endpoints.projects()) });
    const { data: attention, error: attentionError } = useTaskAttention();
    const signals = useMemo(() => new Map(attention?.items.map(item => [item.task_id, item])), [attention]);
    const rows = useMemo(() => filterMyTasks(tasks.data ?? [], search).filter(task => {
        const item = signals.get(task.id);
        return signal === "all" || Boolean(item && (signal === "new" ? item.is_new : signal === "overdue" ? item.is_overdue : signal === "updated" ? item.is_updated : item.is_new || item.is_overdue || item.is_updated));
    }), [tasks.data, search, signal, signals]);
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    const current = Math.min(page, pages - 1);
    return <Page>
        <header className="flex flex-wrap items-end justify-between gap-3">
            <div><h1 className="text-[22px] font-semibold tracking-[-0.03em] text-primary">Мои задачи</h1><p className="text-[13px] text-muted">Проектные и разовые задачи, где вы исполнитель, постановщик или наблюдатель</p></div>
            <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>Новая задача</Button>
        </header>
        <div className="-mx-1 flex min-w-0 gap-2 overflow-x-auto overscroll-x-contain px-1 pb-1" role="group" aria-label="Непросмотренные события задач">
            {([{ key: "all", label: "Все", count: undefined }, { key: "unread", label: "Требуют просмотра", count: attention?.total }, { key: "new", label: "Новые", count: attention?.new_tasks }, { key: "overdue", label: "Нарушен срок", count: attention?.overdue_tasks }, { key: "updated", label: "Комментарии и изменения", count: attention?.updated_tasks }]).map(item =>
                <Button key={item.key} size="sm" className="shrink-0 whitespace-nowrap" variant={signal === item.key ? "primary" : "ghost"} disabled={item.key !== "all" && !attention} aria-pressed={signal === item.key} onClick={() => { setSearchParams(current => { const next = new URLSearchParams(current); if (item.key === "all") next.delete("attention"); else next.set("attention", item.key); return next; }, { replace: true }); if (item.key !== "all") setIncludeClosed(true); setPage(0); }}>{item.label}{item.count !== undefined && ` · ${item.count}`}</Button>)}
        </div>
        {attentionError && <p role="alert" className="text-xs text-danger">Счётчики непросмотренных изменений недоступны.</p>}
        <div className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-3">
            <div className="flex flex-wrap gap-1" role="group" aria-label="Моя роль в задаче">{MY_TASK_ROLES.map(item => <Button key={item.value} size="sm" variant={role === item.value ? "primary" : "ghost"} aria-pressed={role === item.value} onClick={() => { setRole(item.value); setPage(0); }}>{item.label}</Button>)}</div>
            <div className="flex min-w-0 flex-auto flex-wrap items-center gap-3">
                <MyTaskProjectFilter projects={projects.data ?? []} projectId={projectId}
                    disabled={projects.isPending || (!projects.data && projects.isError)} loading={projects.isPending}
                    onChange={id => { setProjectId(id); setPage(0); }} />
                <label className="flex items-center gap-2 text-[13px] text-secondary"><input type="checkbox" checked={includeClosed} onChange={event => { setIncludeClosed(event.target.checked); setPage(0); }} />Показывать закрытые</label>
                <Input className="max-w-xs sm:ml-auto" aria-label="Поиск по моим задачам" placeholder="Номер, название или проект" value={search} onChange={event => { setSearch(event.target.value); setPage(0); }} />
            </div>
        </div>
        {projects.error && <ErrorMessage title="Не удалось загрузить проекты" message={(projects.error as Error).message} action={<Button size="sm" onClick={() => void projects.refetch()}>Повторить загрузку проектов</Button>} />}
        {tasks.error && <ErrorMessage message={(tasks.error as Error).message} />}
        {tasks.isPending && <Skeleton className="h-48 w-full" />}
        {tasks.data && rows.length === 0 && <EmptyState icon={<ListChecks size={24} />} title="Задач пока нет" description="Создайте разовую задачу или смените фильтры." />}
        {rows.length > 0 && <div className="overflow-x-auto rounded-lg md:border md:border-line-subtle"><table className="mobile-data-table w-full text-left text-[13px] md:min-w-[850px]">
            <thead className="bg-surface/35 text-xs text-muted"><tr>{["Номер", "Задача", "Проект", "Статус", "Исполнитель", "Моя роль", "Срок"].map(label => <th key={label} className="p-3 font-medium">{label}</th>)}</tr></thead>
            <tbody>{rows.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE).map(task => {
                const executor = executorOf(task); const item = signals.get(task.id);
                return <tr key={task.id} onClick={() => setSelectedTaskId(task.id)} className={`cursor-pointer border-t border-line-subtle hover:bg-surface/35 ${selectedTaskId === task.id ? "bg-accent/10" : ""}`}>
                    <td className="p-3 font-mono text-xs text-muted">{task.key}{item && (item.is_new || item.is_updated || item.is_overdue) && <span title="Есть непросмотренные изменения" className="ml-1 text-accent">●</span>}</td>
                    <td className="max-w-sm p-3"><button type="button" onClick={event => { event.stopPropagation(); setSelectedTaskId(task.id); }} className="text-left font-medium text-primary">{task.title}</button><span className="ml-2"><PriorityBadge priority={task.priority} /></span></td>
                    <td data-label="Проект" className="p-3"><TaskSourceLabel context={task.context} /></td><td data-label="Статус" className="p-3 text-secondary">{task.state_label}</td>
                    <td data-label="Исполнитель" className="p-3 text-secondary">{executor ? fullName(executor) : task.assignee || "—"}</td><td data-label="Моя роль" className="p-3 text-muted">{task.my_roles.map(value => MY_ROLE_LABELS[value]).join(", ")}</td><td className="p-3"><DueDate value={task.due_date} isDone={isClosedStatus(task.state)} /></td>
                </tr>;
            })}</tbody>
        </table></div>}
        {pages > 1 && <nav aria-label="Страницы задач" className="flex items-center justify-start gap-3"><Button size="sm" disabled={current === 0} onClick={() => setPage(current - 1)}>Назад</Button><span className="text-xs text-muted">{current + 1} / {pages}</span><Button size="sm" disabled={current + 1 === pages} onClick={() => setPage(current + 1)}>Дальше</Button></nav>}
        {creating && <CreatePersonalTaskDialog onClose={() => setCreating(false)} onCreated={task => setSelectedTaskId(task.id)} />}
    </Page>;
}
