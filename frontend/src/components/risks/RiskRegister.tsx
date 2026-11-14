import { CalendarClock, Link2, UserRound } from "lucide-react";
import { formatDayMonth } from "@/lib/dates";
import type { ProjectMember, Task } from "@/lib/types";
import { PROBABILITY_LABELS, IMPACT_LABELS, RISK_STATUS_LABELS, isRiskReviewDue, type ProjectRisk } from "@/lib/risks";
import { cn } from "@/lib/cn";
import { RiskBadge } from "@/components/risks/RiskBadge";
import { UserIdentity } from "@/components/users/UserIdentity";

interface Props {
    risks: ProjectRisk[];
    members: ProjectMember[];
    tasks: Task[];
    onOpen: (riskId: number) => void;
}

export function RiskRegister({ risks, members, tasks, onOpen }: Props) {
    const people = new Map(members.map((member) => [member.user.id, member.user]));
    const taskKeys = new Map(tasks.map((task) => [task.id, task.key]));
    return (
        <ul aria-label="Реестр рисков" className="divide-y divide-line-subtle">
            {risks.map((risk) => (
                <li key={risk.id}>
                    <button
                        type="button" onClick={() => onOpen(risk.id)} aria-label={`Открыть ${risk.key}: ${risk.title}`}
                        className="group flex w-full flex-col gap-2 rounded-control px-3 py-4 text-left transition-colors hover:bg-white/[0.025] focus-visible:outline-2 focus-visible:outline-accent"
                    >
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                            <RiskBadge level={risk.risk_level} />
                            <span className="font-mono text-[10px] text-muted">{risk.key}</span>
                            <span className={cn("ml-auto text-[11px]", risk.status === "OCCURRED" ? "text-danger" : "text-muted")}>{RISK_STATUS_LABELS[risk.status]}</span>
                        </div>
                        <h3 className="text-[14px] font-medium leading-snug break-words text-primary group-hover:text-accent">{risk.title}</h3>
                        <p className="text-[11px] text-muted">{PROBABILITY_LABELS[risk.probability]} вероятность × {IMPACT_LABELS[risk.impact].toLowerCase()} влияние</p>
                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-muted">
                            {risk.owner_user_id && people.has(risk.owner_user_id)
                                ? <UserIdentity user={people.get(risk.owner_user_id)} className="max-w-full" />
                                : <span className="inline-flex items-center gap-1"><UserRound size={11} aria-hidden="true" />{risk.owner_user_id ? "Участник проекта" : "Не назначен"}</span>}
                            {risk.review_date && <span className={cn("inline-flex items-center gap-1", isRiskReviewDue(risk) && "text-warning")}><CalendarClock size={11} aria-hidden="true" />Контроль: {formatDayMonth(risk.review_date)}{isRiskReviewDue(risk) && " · требуется пересмотр"}</span>}
                            {risk.task_id && <span className="inline-flex items-center gap-1 font-mono"><Link2 size={11} aria-hidden="true" />{taskKeys.get(risk.task_id) ?? `Задача #${risk.task_id}`}</span>}
                        </div>
                    </button>
                </li>
            ))}
        </ul>
    );
}

