import { useInfiniteQuery, useIsMutating, useMutation, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { api, ApiError, endpoints } from "@/lib/api";
import { useCurrentUser } from "@/lib/useAuth";
import type { AgentFile } from "@/lib/types";
import type { AgentConversation, AgentConversationPage, AgentMessage, AgentMessageAccepted, AgentMessagePage } from "@/lib/types";

export function useProjectConversations(projectId: number, conversationId: number | null) {
    const queryClient = useQueryClient();
    const userId = useCurrentUser().data?.id;
    const baseKey = ["projects", projectId, "aris", userId] as const;
    const mutating = useIsMutating({ mutationKey: baseKey }) > 0;
    const messageKey = (id: number | null) => [...baseKey, "messages", id] as const;

    const conversations = useInfiniteQuery({
        queryKey: [...baseKey, "conversations"],
        enabled: userId != null,
        initialPageParam: 0,
        queryFn: ({ pageParam }) => api.get<AgentConversationPage>(`${endpoints.projectArisConversations(projectId)}?offset=${pageParam}`),
        getNextPageParam: (lastPage) => lastPage.next_offset ?? undefined,
    });

    const messages = useInfiniteQuery({
        queryKey: messageKey(conversationId),
        enabled: conversationId != null && userId != null,
        initialPageParam: null as number | null,
        queryFn: ({ pageParam }) => api.get<AgentMessagePage>(
            `${endpoints.projectArisMessages(projectId, conversationId!)}${pageParam == null ? "" : `?before_id=${pageParam}`}`,
        ),
        getNextPageParam: (lastPage) => lastPage.next_before_id ?? undefined,
        refetchInterval: (query) => query.state.data?.pages[0].items.some(
            (message) => message.status === "queued" || message.status === "processing",
        ) ? 1500 : 10000,
    });

    function remember(id: number, incoming: AgentMessage[]) {
        queryClient.setQueryData<InfiniteData<AgentMessagePage, number | null>>(messageKey(id), (previous) => {
            if (!previous) return { pages: [{ items: incoming, next_before_id: null }], pageParams: [null] };
            const byId = new Map(previous.pages[0].items.map((message) => [message.id, message]));
            incoming.forEach((message) => byId.set(message.id, message));
            return { ...previous, pages: [{ ...previous.pages[0], items: [...byId.values()].sort((a, b) => a.id - b.id) }, ...previous.pages.slice(1)] };
        });
        void queryClient.invalidateQueries({ queryKey: messageKey(id) });
    }

    const create = useMutation({
        mutationKey: [...baseKey, "create"],
        mutationFn: () => api.post<AgentConversation>(endpoints.projectArisConversations(projectId)),
        onSuccess: () => { void queryClient.invalidateQueries({ queryKey: [...baseKey, "conversations"] }); },
    });

    const send = useMutation({
        mutationKey: [...baseKey, "send"],
        mutationFn: ({ id, content, requestId, fileIds = [] }: { id: number; content: string; requestId: string; fileIds?: string[] }) =>
            api.post<AgentMessageAccepted>(endpoints.projectArisMessages(projectId, id), { content, request_id: requestId, file_ids: fileIds }),
        retry: (count, error) => count < 1 && (!(error instanceof ApiError) || error.status >= 500),
        onSuccess: (result, { id }) => {
            remember(id, [result.user_message, result.assistant_message]);
            void queryClient.invalidateQueries({ queryKey: [...baseKey, "conversations"] });
        },
    });

    const retry = useMutation({
        mutationKey: [...baseKey, "retry"],
        mutationFn: ({ id, messageId }: { id: number; messageId: number }) =>
            api.post<AgentMessage>(endpoints.projectArisMessageRetry(projectId, id, messageId)),
        onSuccess: (message, { id }) => remember(id, [message]),
    });

    const decide = useMutation({
        mutationKey: [...baseKey, "decide"],
        mutationFn: ({ id, actionId, decision }: { id: number; actionId: string; decision: "approve" | "reject" }) =>
            api.post(endpoints.projectArisActionDecision(projectId, id, actionId), { decision }),
        onSettled: () => {
            // Даже при потере HTTP-ответа решение могло сохраниться на сервере.
            void queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
        },
    });

    const upload = useMutation({
        mutationKey: [...baseKey, "upload"],
        mutationFn: ({ id, file }: { id: number; file: File }) => {
            const body = new FormData();
            body.append("file", file);
            return api.postForm<AgentFile>(endpoints.projectArisFiles(projectId, id), body);
        },
    });
    const removeUpload = useMutation({
        mutationKey: [...baseKey, "removeUpload"],
        mutationFn: ({ id, fileId }: { id: number; fileId: string }) => api.delete(`${endpoints.projectArisFiles(projectId, id)}/${fileId}`),
    });

    return { conversations, messages, create, send, retry, decide, upload, removeUpload, mutating };
}
