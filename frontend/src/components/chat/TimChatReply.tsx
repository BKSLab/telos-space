import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Loader2 } from "lucide-react";
import { api, ApiError, endpoints } from "@/lib/api";
import { chatKeys, isAgentThinking, type ChatHistory, type ChatMessage } from "@/lib/projectChat";
import type { AgentToolRun } from "@/lib/types";
import { useProjectChat } from "@/lib/useProjectChat";
import { useRenderedMarkdown } from "@/lib/useRenderedMarkdown";
import { actionSources } from "@/lib/agentCards";
import { invalidateAfterTimAction } from "@/lib/useTimConversations";
import { AgentActionCard } from "@/components/projects/AgentActionCard";
import { AgentEntityCard } from "@/components/agent/AgentEntityCard";

/** Ответ Ариса — Markdown; HTML очищается DOMPurify внутри общего рендерера. */
export function TimMarkdown({ content }: { content: string }) {
    const html = useRenderedMarkdown(content);
    return <div className="markdown-body min-w-0 text-[14px] leading-relaxed break-words text-primary [overflow-wrap:anywhere]" dangerouslySetInnerHTML={{ __html: html }} />;
}

/** Пока Арис готовит ответ, строка показывает, что он делает прямо сейчас (docs/tim-agent.md). */
export const TIM_THINKING = "Арис думает…";
export const TIM_PROGRESS_HINT = "Что Арис делает прямо сейчас. Ответ появится здесь же, когда он закончит.";

/** Индикатор ответа: живой статус («Читаю задачу EVL-1…») или «думает», пока статуса нет.
 *  Высота строки постоянна, длинный статус обрезается — смена текста не двигает ленту. */
export function TimThinking({ progress }: { progress?: string | null }) {
    const label = progress?.trim() || TIM_THINKING;
    return <p role="status" aria-live="polite" aria-atomic="true" title={`${label}\n${TIM_PROGRESS_HINT}`}
        className="brand-ai-progress flex h-6 min-w-0 items-center gap-2.5 text-[12px] leading-6">
        <Loader2 size={13} className="shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        <span key={label} className="min-w-0 truncate animate-[overlay-in_160ms_ease-out] motion-reduce:animate-none">{label}</span>
    </p>;
}

function decisionError(error: unknown): string {
    if (error instanceof ApiError) {
        if (error.status === 409) return "Не получилось: данные изменились после предложения или решение уже принято. Отклоните карточку и попросите Ариса заново.";
        if (error.status === 404) return "Это действие вам недоступно: нужны права на само изменение.";
        if (error.status === 403) return "У вас доступ только на чтение — решать по действиям Ариса нельзя.";
    }
    return (error as Error)?.message ?? "Не удалось сохранить решение.";
}

/** Тело сообщения Ариса в чате проекта: «думает», ошибка, ответ, карточки и действия (docs/tim-agent.md, 2). */
export function TimChatReply({ message }: { message: ChatMessage }) {
    const { projectId, projectKey, userId, canWrite } = useProjectChat();
    const queryClient = useQueryClient();
    const [error, setError] = useState<string | null>(null);
    const agent = message.agent;
    const project = { id: projectId, key: projectKey ?? String(projectId) };
    const decide = useMutation({
        mutationFn: ({ actionId, decision }: { actionId: string; decision: "approve" | "reject" }) =>
            api.post<AgentToolRun>(endpoints.projectTimActionDecision(projectId, actionId), { decision }),
        onMutate: () => setError(null),
        onSuccess: (action) => {
            // Карточка меняется сразу; message.updated от сервера придёт следом с новой версией.
            queryClient.setQueriesData<ChatHistory>({ queryKey: chatKeys.messages(projectId, userId) }, (old) => old && ({
                ...old, messages: old.messages.map((row) => row.id === message.id && row.agent ? { ...row, agent: { ...row.agent, actions: row.agent.actions.map((item) => item.id === action.id ? action : item) } } : row),
            }));
            if (action.status === "completed") invalidateAfterTimAction(queryClient);
        },
        onError: (failure) => setError(decisionError(failure)),
    });

    if (isAgentThinking(message)) return <TimThinking progress={agent?.progress} />;
    const failed = agent?.status === "failed";
    const answered = agent?.status === "completed" || failed;
    const requester = agent?.requester ?? null;
    const mine = requester?.id === userId;
    const actions = agent?.actions ?? [];
    const sources = (agent?.sources ?? []).filter((source) => !actions.some((action) => actionSources(action).some((item) => item.source_id === source.source_id)));
    return <div className="min-w-0 space-y-3">
        {failed
            ? <div className="flex items-start gap-2 text-[13px] text-danger"><CircleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" /><div><p className="whitespace-pre-wrap">{message.content || "Не получилось ответить. Спросите ещё раз."}</p><p className="mt-1 text-[11px] text-muted">Чтобы спросить снова, начните новое сообщение с «Арис».</p></div></div>
            : message.content ? <TimMarkdown content={message.content} /> : null}
        {sources.length > 0 && <div className="grid gap-3 sm:grid-cols-2">{sources.map((source) => <AgentEntityCard key={source.source_id} source={source} project={project} />)}</div>}
        {actions.map((action) => <AgentActionCard key={action.id} action={action} project={project} sources={agent?.sources ?? []}
            pending={decide.isPending && decide.variables?.actionId === action.id}
            onDecision={(decision) => decide.mutate({ actionId: action.id, decision })}
            rules={{
                enabled: answered,
                canApprove: canWrite,
                canReject: canWrite || mine,
                disabledHint: "Решить можно, когда Арис допишет ответ",
                approveHint: "Выполнится от вашего имени и с вашими правами — изменение сразу увидит вся команда. Отменить можно только вручную",
                note: mine && !canWrite
                    ? "Подтвердить может участник с правом на изменения в проекте. Вы можете только отклонить."
                    : mine || !requester
                        ? "Арис предлагает изменение. Оно выполнится только после подтверждения; отменить потом можно только вручную."
                        : `Просил ${requester.display_name}. Подтвердить может он или участник с правами на это действие — выполнится от имени подтвердившего.`,
            }}
        />)}
        {error && <p role="alert" className="text-[12px] text-danger">{error}</p>}
    </div>;
}
