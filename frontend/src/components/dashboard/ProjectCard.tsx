import { Link } from "react-router-dom";
import { useMemo } from "react";
import { AlertTriangle } from "lucide-react";
import type { DashboardProject } from "@/lib/types";
import { formatDayMonth } from "@/lib/dates";
import { useRenderedMarkdown } from "@/lib/useRenderedMarkdown";
import { Card } from "@/components/ui/Card";
import { ProgressBar } from "@/components/ui/Progress";
import { ProjectIcon } from "@/components/projects/ProjectIcon";
import { ProjectStatusBadge } from "@/components/ui/Badge";

/**
 * Карточка проекта по макету раздела 6: название, краткое описание, прогресс,
 * счётчики и ближайший срок. Красный цвет — только при реальной просрочке.
 */
export function ProjectCard({ project }: { project: DashboardProject }) {
    const percent = Math.round(project.completion_rate * 100);
    const descriptionHtml = useRenderedMarkdown(project.description_md ?? "");
    const description = useMemo(() => {
        const document = new DOMParser().parseFromString(descriptionHtml, "text/html");
        document.querySelectorAll("p, li, h1, h2, h3, h4, h5, h6, br, pre").forEach(element => element.append(" "));
        return (document.body.textContent ?? "").replace(/\s+/g, " ").trim();
    }, [descriptionHtml]);

    return (
        <Card
            interactive
            className="relative min-w-0 overflow-hidden focus-within:border-accent/55 focus-within:shadow-selected"
        >
            <span
                aria-hidden="true"
                style={{ backgroundColor: project.color }}
                className="absolute top-4 bottom-4 left-0 w-px opacity-80"
            />
            <Link
                to={`/projects/${project.key}`}
                className="flex h-full min-w-0 flex-col gap-4 p-4 pl-[17px] outline-none sm:p-5 sm:pl-[21px]"
            >
                <div className="flex min-w-0 items-start justify-between gap-3">
                    <div className="flex min-w-0 items-start gap-2.5">
                        <ProjectIcon project={project} size={36} />
                        <div className="flex min-w-0 flex-col gap-0.5">
                            <h3 className="line-clamp-2 text-[16px] leading-snug font-semibold tracking-[-0.015em] break-words text-primary">
                                {project.name}
                            </h3>
                            <span className="font-mono text-[12px] text-muted">{project.key}</span>
                        </div>
                    </div>
                    <ProjectStatusBadge status={project.status} className="shrink-0 text-[12px]" />
                </div>

                {description && (
                    <p className="line-clamp-2 text-[14px] leading-relaxed text-muted">{description}</p>
                )}

                <div className="mt-auto flex flex-col gap-2.5">
                    <div className="flex items-baseline justify-between gap-2">
                        <span className="text-[12px] text-muted">{project.done_tasks} из {project.total_tasks} задач</span>
                        <span className="text-[24px] leading-none font-semibold tracking-[-0.03em] text-primary tabular-nums">{project.total_tasks > 0 ? `${percent}%` : "—"}</span>
                    </div>
                    <ProgressBar
                        value={project.completion_rate}
                        color={project.color}
                        label={`Прогресс проекта ${project.name}`}
                        className="h-2"
                    />
                    <div className="flex flex-wrap items-center justify-between gap-2 text-[12px]">
                        <span className="text-muted">
                            {project.in_progress_tasks} в работе
                        </span>
                        {project.overdue_tasks > 0 && <span className="inline-flex items-center gap-1 text-danger"><AlertTriangle size={13} aria-hidden="true" />{project.overdue_tasks} просрочено</span>}
                    </div>
                </div>

                {project.risks && (project.risks.high_risks > 0 || project.risks.occurred_risks > 0) && (
                    <p className="inline-flex flex-wrap items-center gap-1 text-[12px] text-danger"><AlertTriangle size={13} aria-hidden="true" />{project.risks.high_risks} высоких рисков{project.risks.occurred_risks > 0 && ` · ${project.risks.occurred_risks} реализовались`}</p>
                )}
                {project.next_due_date && (
                    <p className="text-[12px] text-muted">
                        Ближайший срок:{" "}
                        <span className="font-mono text-secondary">
                            {formatDayMonth(project.next_due_date)}
                        </span>
                    </p>
                )}
            </Link>
        </Card>
    );
}
