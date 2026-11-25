import { useQuery, type QueryKey } from "@tanstack/react-query";
import { createContext, useContext } from "react";
import { api, ApiError, queryKeys } from "@/lib/api";
import { contextApi } from "@/lib/projectKnowledge";

export const RepositoryAccess = createContext<(id: number) => void>(() => {});
export const repositoryPrefix = (project: number) => [...queryKeys.project(project), "git", "workspace"];
export const repositoryKey = (project: number, connection: number | string = "all") => [...repositoryPrefix(project), connection];
export const repositoryPath = (project: number, connection: number) => `${contextApi.git(project)}/connections/${connection}`;

/** Every request is abortable and its cache identity includes the immutable source scope. */
export function useRepositoryQuery<T>({ project, connection, sha, kind, params, enabled = true }: {
    project: number; connection: number; sha: string; kind: string; params?: Record<string, string>; enabled?: boolean;
}) {
    const revoke = useContext(RepositoryAccess);
    const queryKey: QueryKey = [...repositoryKey(project, connection), sha, kind, params ?? {}];
    return useQuery<T>({ queryKey, enabled: enabled && connection > 0 && Boolean(sha), retry: false, staleTime: 0,
        queryFn: async ({ signal }) => {
            const query = new URLSearchParams({ sha, ...params });
            try { return await api.get<T>(`${repositoryPath(project, connection)}/${kind}?${query}`, { signal }); }
            catch (error) { if (error instanceof ApiError && [401, 403].includes(error.status)) revoke(connection); throw error; }
        },
    });
}
