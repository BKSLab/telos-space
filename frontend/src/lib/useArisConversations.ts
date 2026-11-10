import { useInfiniteQuery, useIsMutating, useMutation, useQueryClient, type InfiniteData, type QueryClient } from "@tanstack/react-query";
import { api, ApiError, endpoints } from "@/lib/api";
import { useCurrentUser } from "@/lib/useAuth";
import type { AgentConversation, AgentConversationPage, AgentFile, AgentMessage, AgentMessageAccepted, AgentMessagePage, AgentToolRun, AgentPageContext } from "@/lib/types";

/** После выполненного действия Ариса обновляем то, что он мог поменять: проекты, задачи, документы, ИСР и доски. */
export function invalidateAfterArisAction(queryClient: QueryClient) {
    void queryClient.invalidateQueries({ predicate: (query) => {
        const [kind] = query.queryKey;
        return kind === "projects" || kind === "tasks" || kind === "documents" || kind === "dashboard" || kind === "my-tasks" || kind === "calendar" || kind === "boards" || kind === "wbs" || kind === "project-risks";
    } });
}

/** Личный диалог с Арисом в виджете (docs/agent.md): не привязан к проекту, виден только владельцу. */
export function useArisConversations(conversationId: number | null, enabled = true) {
    const queryClient = useQueryClient();
    const userId = useCurrentUser().data?.id;
    const baseKey = ["aris", userId] as const;
    const mutating = useIsMutating({ mutationKey: baseKey }) > 0;
    const messageKey = (id: number | null) => [...baseKey, "messages", id] as const;

    const conversations = useInfiniteQuery({
        queryKey: [...baseKey, "conversations"],
        enabled: enabled && userId != null,
        initialPageParam: 0,
        queryFn: ({ pageParam }) => api.get<AgentConversationPage>(`${endpoints.arisConversations()}?offset=${pageParam}&limit=30`),
        getNextPageParam: (lastPage) => lastPage.next_offset ?? undefined,
    });

    const messages = useInfiniteQuery({
        queryKey: messageKey(conversationId),
        enabled: enabled && conversationId != null && userId != null,
        initialPageParam: null as number | null,
        queryFn: ({ pageParam }) => api.get<AgentMessagePage>(
            `${endpoints.arisMessages(conversationId!)}${pageParam == null ? "" : `?before_id=${pageParam}`}`,
        ),
        getNextPageParam: (lastPage) => lastPage.next_before_id ?? undefined,
        // Ответ готовится в фоне: пока Арис думает — опрашиваем чаще.
        refetchInterval: (query) => query.state.data?.pages[0].items.some(
            (message) => message.status === "queued" || message.status === "processing",
        ) ? 1500 : 15000,
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
        mutationFn: () => api.post<AgentConversation>(endpoints.arisConversations()),
        onSuccess: () => { void queryClient.invalidateQueries({ queryKey: [...baseKey, "conversations"] }); },
    });

    const send = useMutation({
        mutationKey: [...baseKey, "send"],
        mutationFn: ({ id, content, requestId, fileIds = [], pageContext }: { id: number; content: string; requestId: string; fileIds?: string[]; pageContext: AgentPageContext }) =>
            api.post<AgentMessageAccepted>(endpoints.arisMessages(id), { content, request_id: requestId, file_ids: fileIds, page_context: pageContext }),
        retry: (count, error) => count < 1 && (!(error instanceof ApiError) || error.status >= 500),
        onSuccess: (result, { id }) => {
            remember(id, [result.user_message, result.assistant_message]);
            void queryClient.invalidateQueries({ queryKey: [...baseKey, "conversations"] });
        },
    });

    const retry = useMutation({
        mutationKey: [...baseKey, "retry"],
        mutationFn: ({ id, messageId }: { id: number; messageId: number }) =>
            api.post<AgentMessage>(endpoints.arisMessageRetry(id, messageId)),
        onSuccess: (message, { id }) => remember(id, [message]),
    });

    const decide = useMutation({
        mutationKey: [...baseKey, "decide"],
        mutationFn: ({ id, actionId, decision }: { id: number; actionId: string; decision: "approve" | "reject" }) =>
            api.post<AgentToolRun>(endpoints.arisActionDecision(id, actionId), { decision }),
        onSettled: (action, _error, { id }) => {
            // После решения Арис снова дописывает ответ — продолжаем опрос. Даже при потере
            // HTTP-ответа решение могло сохраниться на сервере.
            void queryClient.invalidateQueries({ queryKey: messageKey(id) });
            if (!action || action.status === "completed") invalidateAfterArisAction(queryClient);
        },
    });

    const upload = useMutation({
        mutationKey: [...baseKey, "upload"],
        mutationFn: ({ id, file }: { id: number; file: File }) => {
            const body = new FormData();
            body.append("file", file);
            return api.postForm<AgentFile>(endpoints.arisFiles(id), body);
        },
    });
    const removeUpload = useMutation({
        mutationKey: [...baseKey, "removeUpload"],
        mutationFn: ({ id, fileId }: { id: number; fileId: string }) => api.delete(`${endpoints.arisFiles(id)}/${fileId}`),
    });

    return { conversations, messages, create, send, retry, decide, upload, removeUpload, mutating };
}
