import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { Project, ProjectMember } from "@/lib/types";
import { UserIdentity } from "@/components/users/UserIdentity";

export function ProjectPeopleSummary({ project, limit }: { project: Project; limit?: number }) {
    const query = useQuery({
        queryKey: queryKeys.projectMembers(project.id),
        queryFn: () => api.get<ProjectMember[]>(endpoints.projectMembers(project.id)),
    });
    if (query.isPending) return <p role="status" className="text-[12px] text-muted">Загрузка команды…</p>;
    if (query.error) return <p role="alert" className="text-[12px] text-danger">Не удалось загрузить сведения о команде.</p>;
    const members = query.data ?? [];
    const manager = members.find((member) => member.user.id === project.owner_id) ?? members.find((member) => member.role === "OWNER");
    const otherMembers = members.filter(member => member.user.id !== manager?.user.id);
    return (
        <dl className="flex flex-col gap-2 text-[13px]">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1"><dt className="text-muted">Руководитель:</dt><dd className="min-w-0 text-secondary">{manager ? <UserIdentity user={manager.user} /> : "Не указан"}</dd></div>
            {limit === undefined ? <div className="flex flex-wrap gap-x-2"><dt className="text-muted">Команда:</dt><dd className="min-w-0 flex-1 basis-40">
                <Link to={`/projects/${project.key}/team`} aria-label="Открыть команду проекта" className="inline-flex min-h-11 max-w-full flex-wrap items-center gap-x-3 gap-y-1 rounded text-accent hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
                    {members.length ? members.map((member) => <UserIdentity key={member.user.id} user={member.user} className="max-w-full" />) : "Открыть команду"}
                </Link>
            </dd></div> : <div className="flex flex-col gap-1">
                <dt className="text-muted">Участники · {members.length}</dt>
                <dd className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-secondary">
                    {otherMembers.slice(0, limit).map(member => <UserIdentity key={member.user.id} user={member.user} className="max-w-full" />)}
                    {otherMembers.length === 0 && <span className="text-muted">{manager ? "Только руководитель" : "Команда пока пуста"}</span>}
                    {otherMembers.length > limit && <span className="text-muted">+{otherMembers.length - limit}</span>}
                </dd>
            </div>}
        </dl>
    );
}
