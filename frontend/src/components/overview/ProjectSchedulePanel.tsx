import { CalendarClock, Flag } from "lucide-react";
import { Link } from "react-router-dom";
import type { Project, ProjectMilestone } from "@/lib/types";
import { OverviewPanel } from "@/components/dashboard/OverviewCards";
import { Button } from "@/components/ui/Button";
import { DueDate } from "@/components/ui/DueDate";
import { ErrorMessage, Skeleton } from "@/components/ui/States";

export function ProjectSchedulePanel({ project, nextTaskDue, milestones, isPending, error, onRetry }: {
    project: Project;
    nextTaskDue: string | null;
    milestones: ProjectMilestone[] | undefined;
    isPending: boolean;
    error: Error | null;
    onRetry: () => void;
}) {
    const planned = (milestones ?? []).filter(item => item.status === "PLANNED")
        .sort((first, second) => first.due_date.localeCompare(second.due_date) || first.id - second.id);
    const next = planned[0];
    const calendar = `/projects/${project.key}/calendar`;
    return <OverviewPanel title="Сроки и вехи" className="h-full"
        action={<Link to={calendar} className="text-[14px] text-accent hover:text-accent-hover">Календарь</Link>}>
        <dl className="divide-y divide-line-subtle text-[14px]">
            <div className="flex flex-wrap items-center justify-between gap-2 pb-3">
                <dt className="flex items-center gap-2 text-secondary"><CalendarClock size={16} className="text-muted" aria-hidden="true" />Срок проекта</dt>
                <dd className="font-medium text-primary">{project.due_date
                    ? <DueDate value={project.due_date} isDone={project.status === "COMPLETED" || project.status === "ARCHIVED"} className="text-[14px]" />
                    : <span className="font-normal text-muted">Не задан</span>}</dd>
            </div>
            {nextTaskDue && <div className="flex flex-wrap items-center justify-between gap-2 py-3">
                <dt className="text-secondary">Ближайший срок задачи</dt>
                <dd><Link to={calendar} className="rounded hover:underline"><DueDate value={nextTaskDue} className="text-[14px]" /></Link></dd>
            </div>}
            {next && <div className="py-3">
                <dt className="mb-2 flex items-center gap-2 text-secondary"><Flag size={16} className="text-muted" aria-hidden="true" />Ближайшая веха</dt>
                <dd><Link to={calendar} className="flex min-w-0 flex-wrap items-start justify-between gap-x-3 gap-y-1 rounded text-primary hover:text-accent">
                    <span className="min-w-0 flex-1 basis-32 break-words font-medium">{next.title}</span>
                    <DueDate value={next.due_date} className="text-[12px]" />
                </Link></dd>
            </div>}
        </dl>
        {isPending && <div role="status" aria-label="Загрузка вех"><Skeleton className="h-10" /></div>}
        {error && <ErrorMessage title="Вехи недоступны" message={error.message}
            action={<Button size="sm" onClick={onRetry}>Повторить загрузку вех</Button>} />}
        {!isPending && !error && !next && <p className="text-[14px] text-muted">{milestones?.length ? "Все вехи достигнуты" : "Вехи пока не заданы"}</p>}
        {planned.length > 1 && <p className="text-[12px] text-muted">Осталось вех: {planned.length}. Все даты — в календаре.</p>}
    </OverviewPanel>;
}
