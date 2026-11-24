import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { Project } from "@/lib/types";

/**
 * Резолвит проект из адреса вида `/projects/PROJ`. Список проектов невелик и
 * уже закэширован сайдбаром, поэтому поиск по коду не стоит отдельного запроса.
 */
export function useProjectFromRoute() {
    const { projectKey } = useParams<{ projectKey: string }>();
    const projectsQuery = useQuery({
        queryKey: queryKeys.projects,
        staleTime: 30_000,
        queryFn: () => api.get<Project[]>(endpoints.projects()),
    });

    const project = projectsQuery.data?.find(
        (item) => item.key.toUpperCase() === (projectKey ?? "").toUpperCase(),
    );

    return {
        project,
        projectKey: projectKey ?? "",
        isPending: projectsQuery.isPending,
        error: projectsQuery.error,
        isMissing: !projectsQuery.isPending && !projectsQuery.error && project === undefined,
    };
}
