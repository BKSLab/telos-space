import { Link } from "react-router-dom";
import { useId, useMemo, type CSSProperties } from "react";
import { AlertTriangle, ArrowUpRight, CalendarDays, ListChecks, ShieldAlert } from "lucide-react";
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
            className="group/project h-full min-w-0 overflow-hidden focus-within:border-accent/55 focus-within:shadow-selected"
        >
            <Link
                to={`/projects/${project.key}`}
                aria-labelledby={titleId}
                className="flex h-full min-w-0 flex-col gap-3.5 p-4 outline-none sm:p-4.5"
            >
                <div className="flex min-w-0 items-start gap-3">
                    <span className="dashboard-project-icon" style={{ "--project-icon-color": project.color } as CSSProperties}><ProjectIcon project={project} size={48} /></span>
                    <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 items-start gap-2">
                            <h3 id={titleId} title={project.name} className="line-clamp-2 min-w-0 flex-1 text-[16px] leading-snug font-semibold tracking-[-0.015em] text-primary [overflow-wrap:anywhere]">
                                {project.name}
                            </h3>
                            <ArrowUpRight size={15} className="mt-0.5 shrink-0 text-muted transition-colors group-hover/project:text-accent" aria-hidden="true" />
                        </div>
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
                        className="h-1.5 flex-1 bg-white/[0.09]"
                    />
                    <span className="min-w-9 text-right font-mono text-[13px] font-semibold text-primary tabular-nums">{project.total_tasks > 0 ? `${percent}%` : "—"}</span>
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
                    {project.next_due_date && <span className="inline-flex flex-wrap items-center gap-1 sm:ml-auto">
                        <CalendarDays size={13} aria-hidden="true" /><span className="sr-only">Ближайший срок:</span>
                        <time dateTime={project.next_due_date} title={formatFullDate(project.next_due_date)} className="font-mono text-secondary">{formatDayMonth(project.next_due_date)}</time>
                    </span>}
                </div>
            </Link>
        </Card>
    );
}
