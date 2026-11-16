import { AlertTriangle, CheckCircle2, FolderKanban, ListTodo, ShieldAlert } from "lucide-react";
import type { Dashboard } from "@/lib/types";
import { MetricCard, MetricGrid } from "@/components/dashboard/OverviewCards";

/** Актуальные показатели всего портфеля; фильтры каталога не меняют их контекст. */
export function PortfolioMetrics({ data }: { data: Dashboard }) {
    const assessedProjects = data.projects.filter(project => project.risks !== undefined);
    const activeRisks = assessedProjects.reduce((total, project) => total + project.risks!.active_risks, 0);
    const highRisks = assessedProjects.reduce((total, project) => total + project.risks!.high_risks, 0);
    const riskCoverage = assessedProjects.length < data.projects.length
        ? `Оценка: ${assessedProjects.length} из ${data.projects.length} проектов`
        : `Высоких: ${highRisks}`;

    return <section aria-label="Показатели портфеля" className="flex min-w-0 flex-col gap-2">
        <MetricGrid className="sm:grid-cols-5 [&>.dashboard-metric]:gap-2 [&>.dashboard-metric]:p-3 [&>.dashboard-metric:last-child]:col-span-2 sm:[&>.dashboard-metric:last-child]:col-span-1">
            <MetricCard label="Проекты" value={data.totals.total_projects} hint={`${data.totals.active_projects} в работе`} icon={<FolderKanban size={18} />} />
            <MetricCard label="Задачи" value={data.totals.total_tasks} hint={`${data.totals.in_progress_tasks} в работе`} icon={<ListTodo size={18} />} />
            <MetricCard label="Выполнено" value={data.totals.done_tasks} hint={`Из ${data.totals.total_tasks} задач`} icon={<CheckCircle2 size={18} />} tone="success" />
            <MetricCard label="Просрочено" value={data.totals.overdue_tasks} hint={data.totals.overdue_tasks > 0 ? "Открытых задач" : "Сроки под контролем"} icon={<AlertTriangle size={18} />} tone={data.totals.overdue_tasks > 0 ? "danger" : "default"} />
            <MetricCard label="Активные риски" value={assessedProjects.length > 0 ? activeRisks : "—"} hint={assessedProjects.length > 0 ? riskCoverage : "Нет данных об оценке"} icon={<ShieldAlert size={18} />} tone={highRisks > 0 ? "danger" : activeRisks > 0 ? "warning" : "default"} />
        </MetricGrid>
        <p className="text-[12px] leading-relaxed text-muted">Показатели по всем доступным проектам. Разовые задачи показаны отдельно.</p>
    </section>;
}
