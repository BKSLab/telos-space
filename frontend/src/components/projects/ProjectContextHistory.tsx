import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/Button";
import { Section } from "@/components/ui/Card";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { UserIdentity } from "@/components/users/UserIdentity";
import { api, endpoints, queryKeys } from "@/lib/api";
import { projectContextLabel } from "@/lib/projectContext";
import type { ProjectContextHistoryPage } from "@/lib/types";

export function ProjectContextHistory({ projectId }: { projectId: number }) {
    const [page, setPage] = useState({ projectId, offset: 0 });
    const offset = page.projectId === projectId ? page.offset : 0;
    const query = useQuery({
        queryKey: queryKeys.projectContextHistory(projectId, offset),
        queryFn: () => api.get<ProjectContextHistoryPage>(endpoints.projectContextHistory(projectId, offset)),
    });
    return (
        <Section title="История паспорта">
            {query.isPending && <Skeleton className="h-16 w-full" />}
            {query.error && <ErrorMessage message={query.error.message} action={<Button onClick={() => void query.refetch()}>Повторить</Button>} />}
            {query.data?.total === 0 && <p className="text-[13px] text-muted">История появится после сохранения паспорта. Прежние авторы и даты правок неизвестны.</p>}
            <ol className="divide-y divide-line-subtle">
                {query.data?.items.map((change) => (
                    <li key={change.revision} className="flex flex-col gap-1.5 py-3 text-[13px]">
                        <p className="font-medium text-secondary">Версия {change.revision}{change.is_initial ? " · Исходное состояние" : ""}</p>
                        {change.changed_fields.length > 0 && <p className="text-secondary">{change.changed_fields.map(projectContextLabel).join(", ")}</p>}
                        <p className="text-[12px] text-muted">
                            {change.changed_by_name
                                ? <UserIdentity userId={change.changed_by_user_id} name={change.changed_by_name} />
                                : "Автор прежних правок неизвестен"}
                            {" · "}<time dateTime={change.created_at}>{new Date(change.created_at).toLocaleString("ru-RU")}</time>
                            {change.is_initial && change.changed_by_user_id === null ? " · дата фиксации исходного состояния" : ""}
                        </p>
                    </li>
                ))}
            </ol>
            {query.data && (offset > 0 || query.data.has_more) && <nav aria-label="Страницы истории паспорта" className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" disabled={offset === 0} onClick={() => setPage({ projectId, offset: Math.max(0, offset - 20) })}>Новее</Button>
                <Button size="sm" disabled={query.data.next_offset === null} onClick={() => {
                    if (query.data.next_offset !== null) setPage({ projectId, offset: query.data.next_offset });
                }}>Раньше</Button>
            </nav>}
        </Section>
    );
}
