import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, endpoints, queryKeys } from "./api";
import { PROJECT_FILE_PAGE_SIZE, projectFilesPollInterval } from "./projectFiles";
import type { ProjectFile } from "./types";

export function useProjectFiles(projectId: number, search: string) {
    const queryClient = useQueryClient();
    const files = useInfiniteQuery({
        queryKey: queryKeys.projectFileList(projectId, search),
        initialPageParam: 0,
        queryFn: ({ pageParam }) => {
            const params = new URLSearchParams({ limit: String(PROJECT_FILE_PAGE_SIZE), offset: String(pageParam) });
            if (search) params.set("search", search);
            return api.get<ProjectFile[]>(`${endpoints.projectFiles(projectId)}?${params}`);
        },
        getNextPageParam: (lastPage, pages) => lastPage.length === PROJECT_FILE_PAGE_SIZE
            ? pages.length * PROJECT_FILE_PAGE_SIZE : undefined,
        retry: false,
        refetchInterval: (query) => projectFilesPollInterval(query.state.data?.pages.flat(), query.state.status === "error"),
    });

    function refresh() {
        void queryClient.invalidateQueries({ queryKey: queryKeys.projectFiles(projectId) });
        void queryClient.invalidateQueries({ queryKey: queryKeys.projectKnowledgeStatus(projectId) });
    }

    const upload = useMutation({
        mutationFn: async (incoming: File[]) => {
            const errors: string[] = [];
            let uploaded = 0;
            for (const file of incoming) {
                const body = new FormData();
                body.append("file", file);
                try {
                    await api.postForm<ProjectFile>(endpoints.projectFiles(projectId), body);
                    uploaded += 1;
                    refresh();
                } catch (error) {
                    errors.push(`«${file.name}»: ${(error as Error).message}`);
                }
            }
            return { uploaded, errors };
        },
    });
    const retry = useMutation({
        mutationFn: (fileId: number) => api.post<ProjectFile>(endpoints.projectFileRetry(projectId, fileId)),
        onSuccess: refresh,
    });
    const remove = useMutation({
        mutationFn: (fileId: number) => api.delete<void>(endpoints.projectFile(projectId, fileId)),
        onSuccess: (_result, fileId) => {
            queryClient.removeQueries({ queryKey: ["projects", projectId, "files", "detail", fileId] });
            refresh();
        },
    });

    return { files, upload, retry, remove };
}
