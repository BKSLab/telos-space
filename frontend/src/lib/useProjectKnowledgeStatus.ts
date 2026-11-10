import { useQuery } from "@tanstack/react-query";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { KnowledgeStatus } from "@/lib/types";

export function useProjectKnowledgeStatus(projectId: number) {
    return useQuery({
        queryKey: queryKeys.projectKnowledgeStatus(projectId),
        queryFn: () => api.get<KnowledgeStatus>(endpoints.projectKnowledgeStatus(projectId)),
        refetchInterval: query => query.state.data?.ready ? 30_000 : 10_000,
    });
}
