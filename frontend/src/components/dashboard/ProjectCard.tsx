import { Link } from "react-router-dom";
import { useId, useMemo } from "react";
import { AlertTriangle, CalendarDays, ListChecks, ShieldAlert } from "lucide-react";
import type { DashboardProject } from "@/lib/types";
import { formatDayMonth, formatFullDate } from "@/lib/dates";
import { cn } from "@/lib/cn";
import { useRenderedMarkdown } from "@/lib/useRenderedMarkdown";
import { Card } from "@/components/ui/Card";
import { ProgressBar } from "@/components/ui/Progress";
import { ProjectIcon } from "@/components/projects/ProjectIcon";
import { ProjectStatusBadge } from "@/components/ui/Badge";

/**
 * Карточка проекта по макету раздела 6: название, краткое описание, прогресс,
 * счётчики и ближайший срок. Сигналы используют фактические данные проекта.
 */
export function ProjectCard({ project }: { project: DashboardProject }) {
    const titleId = useId();
    const progress = project.total_tasks > 0 ? project.completion_rate : 0;
    const percent = Math.round(Math.min(Math.max(progress, 0), 1) * 100);
    const hasRiskAlert = project.risks && (project.risks.high_risks > 0 || project.risks.occurred_risks > 0);
    const descriptionHtml = useRenderedMarkdown(project.description_md ?? "");
    const description = useMemo(() => {
        const document = new DOMParser().parseFromString(descriptionHtml, "text/html");
        document.querySelectorAll("p, li, h1, h2, h3, h4, h5, h6, br, pre").forEach(element => element.append(" "));
        return (document.body.textContent ?? "").replace(/\s+/g, " ").trim();
    }, [descriptionHtml]);

    return (
        <Card
            interactive
            className="h-full min-w-0 overflow-hidden focus-within:border-accent/55 focus-within:shadow-selected"
        >
            <Link
                to={`/projects/${project.key}`}
                aria-labelledby={titleId}
                className="flex h-full min-w-0 flex-col gap-3 p-4 outline-none"
            >
                <div className="flex min-w-0 items-start gap-3">
                    <ProjectIcon project={project} size={48} />
                    <div className="min-w-0 flex-1">
                        <h3 id={titleId} title={project.name} className="line-clamp-2 text-[15px] leading-snug font-semibold tracking-[-0.015em] text-primary [overflow-wrap:anywhere]">
                            {project.name}
                        </h3>
                        {description && <p className="mt-1 line-clamp-2 text-[12px] leading-relaxed text-muted [overflow-wrap:anywhere]">{description}</p>}
                        <div className="mt-2 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                            <ProjectStatusBadge status={project.status} />
                            <span className="font-mono text-[11px] text-muted [overflow-wrap:anywhere]">{project.key}</span>
                        </div>
                    </div>
                </div>

                <div className="mt-auto flex min-w-0 items-center gap-3">
                    <ProgressBar
                        value={progress}
                        color={project.color}
                        label={`Прогресс проекта ${project.name}`}
                        className="h-1.5 flex-1"
                    />
                    <span className="min-w-8 text-right font-mono text-[12px] font-medium text-primary tabular-nums">{project.total_tasks > 0 ? `${percent}%` : "—"}</span>
                </div>

                <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-line-subtle pt-2.5 text-[12px] text-muted">
                    <span className="inline-flex items-center gap-1.5"><ListChecks size={14} aria-hidden="true" />{project.done_tasks} из {project.total_tasks} задач</span>
                    {project.in_progress_tasks > 0 && <span>{project.in_progress_tasks} в работе</span>}
                    {project.overdue_tasks > 0 && <span className="inline-flex items-center gap-1 text-danger"><AlertTriangle size={13} aria-hidden="true" />{project.overdue_tasks} просрочено</span>}
                    {project.risks && project.risks.total_risks > 0 && <span
                        className={cn("inline-flex min-w-0 items-center gap-1", hasRiskAlert && "text-danger")}
                    ><ShieldAlert size={13} className="shrink-0" aria-hidden="true" />
                        <span>Риски: {project.risks.total_risks}{project.risks.high_risks > 0 && ` · высоких: ${project.risks.high_risks}`}{project.risks.occurred_risks > 0 && ` · реализовалось: ${project.risks.occurred_risks}`}</span>
                    </span>}
                    {project.next_due_date && <span className="inline-flex flex-wrap items-center gap-1">
                        <CalendarDays size={13} aria-hidden="true" /><span>Ближайший срок:</span>
                        <time dateTime={project.next_due_date} title={formatFullDate(project.next_due_date)} className="font-mono text-secondary">{formatDayMonth(project.next_due_date)}</time>
                    </span>}
                </div>
            </Link>
        </Card>
    );
}
