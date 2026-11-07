import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, endpoints, queryKeys } from "./api";
import { PROJECT_FILE_PAGE_SIZE, projectFilesPollInterval } from "./projectFiles";
import type { ProjectFile } from "./types";
import type { FileUploadDraft } from "./files";

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
        mutationFn: async (incoming: FileUploadDraft[]) => {
            const errors: string[] = [];
            const failed: FileUploadDraft[] = [];
            let uploaded = 0;
            for (const draft of incoming) {
                const { file, caption } = draft;
                const body = new FormData();
                body.append("file", file);
                if (caption.trim()) body.append("caption", caption.trim());
                try {
                    await api.postForm<ProjectFile>(endpoints.projectFiles(projectId), body);
                    uploaded += 1;
                    refresh();
                } catch (error) {
                    errors.push(`«${file.name}»: ${(error as Error).message}`);
                    failed.push(draft);
                }
            }
            return { uploaded, errors, failed };
        },
    });
    const caption = useMutation({
        mutationFn: ({ fileId, caption }: { fileId: number; caption: string | null }) =>
            api.patch<ProjectFile>(endpoints.projectFile(projectId, fileId), { caption }),
        onSuccess: refresh,
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

    return { files, upload, retry, remove, caption };
}
