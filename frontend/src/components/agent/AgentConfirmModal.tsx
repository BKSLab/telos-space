import { useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, Clock3, Hourglass, XCircle } from "lucide-react";
import { api, ApiError, endpoints, queryKeys } from "@/lib/api";
import type { AgentPendingAction } from "@/lib/types";
import { formatDateTime } from "@/lib/dates";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { Parameters } from "@/components/projects/AgentActionCard";

/** Ссылка вида `/?confirm=<id>` открывает окно решения по действию агента (#40793). */
export const CONFIRM_PARAM = "confirm";

const STATUS = {
    pending: { label: "Ожидает вашего решения", icon: Clock3, tone: "text-muted" },
    completed: { label: "Выполнено", icon: CheckCircle2, tone: "text-success" },
    rejected: { label: "Отклонено — ничего не изменилось", icon: XCircle, tone: "text-muted" },
    failed: { label: "Не выполнено", icon: CircleAlert, tone: "text-danger" },
    expired: { label: "Срок подтверждения истёк", icon: Hourglass, tone: "text-danger" },
} as const;

/** Текст ошибки для человека: сервер отвечает понятным detail, 404 — отдельная формулировка. */
function errorText(error: unknown): string {
    if (error instanceof ApiError && error.status === 404) {
        return "Действие не найдено. Оно могло быть предложено другому пользователю, или у вас больше нет доступа к проекту.";
    }
    if (error instanceof Error && error.message) return error.message;
    return "Попробуйте ещё раз позже.";
}

/**
 * Окно подтверждения действия агента по ссылке `?confirm=<id>`. Ссылку получает человек,
 * у которого нет чата агента трекера (агента позвал, например, внешний клиент).
 * Решает только тот, от чьего имени шёл запрос; параметры берутся из сохранённого действия.
 */
export function AgentConfirmModal() {
    const [params] = useSearchParams();
    const location = useLocation();
    const navigate = useNavigate();
    const actionId = params.get(CONFIRM_PARAM);
    if (!actionId) return null;
    const close = () => {
        const next = new URLSearchParams(params);
        next.delete(CONFIRM_PARAM);
        navigate({ pathname: location.pathname, search: next.toString(), hash: location.hash }, { replace: true });
    };
    return <AgentConfirmContent key={actionId} actionId={actionId} onClose={close} />;
}

function AgentConfirmContent({ actionId, onClose }: { actionId: string; onClose: () => void }) {
    const queryClient = useQueryClient();
    const [decisionError, setDecisionError] = useState<string | null>(null);
    const query = useQuery({
        queryKey: queryKeys.arisPending(actionId),
        queryFn: () => api.get<AgentPendingAction>(endpoints.arisPending(actionId)),
        retry: false,
    });
    const decide = useMutation({
        mutationFn: (decision: "confirm" | "reject") =>
            api.post<AgentPendingAction>(endpoints.arisPendingDecision(actionId, decision)),
        onSuccess: (action) => {
            setDecisionError(null);
            queryClient.setQueryData(queryKeys.arisPending(actionId), action);
            // Выполненное действие меняет задачи, документы и прочее — пусть экраны перечитают.
            if (action.status === "completed") void queryClient.invalidateQueries();
        },
        onError: (error) => {
            setDecisionError(errorText(error));
            void query.refetch();
        },
    });
    const action = query.data;
    const pending = action?.status === "pending";
    const status = action ? STATUS[action.status] : null;
    const Icon = status?.icon;

    const footer = pending ? (
        <>
            <Button
                disabled={decide.isPending}
                onClick={() => decide.mutate("reject")}
                title="Не выполнять — данные проекта не изменятся, агент узнает об отказе"
            >
                Отклонить
            </Button>
            <Button
                variant="primary"
                disabled={decide.isPending}
                onClick={() => decide.mutate("confirm")}
                title="Выполнить действие сейчас ровно с этими параметрами. Изменение сразу увидит вся команда; отменить можно только вручную"
            >
                {decide.isPending ? "Выполняю…" : "Подтвердить"}
            </Button>
        </>
    ) : action?.status === "expired" ? (
        <>
            <Button onClick={onClose} title="Закрыть окно, ничего не решая">
                Закрыть
            </Button>
            <Button
                disabled={decide.isPending}
                onClick={() => decide.mutate("reject")}
                title="Закрыть просроченное предложение — данные проекта не изменятся"
            >
                Отклонить
            </Button>
        </>
    ) : (
        <Button onClick={onClose} title="Закрыть окно — решение уже сохранено">
            Закрыть
        </Button>
    );

    return (
        <Modal
            title="Подтверждение действия агента"
            description={action ? `Проект «${action.project.name}» (${action.project.key})` : undefined}
            isOpen
            onOpenChange={(open) => !open && onClose()}
            size="md"
            footer={query.isError ? undefined : footer}
        >
            {query.isPending && (
                <div className="flex flex-col gap-2" aria-label="Загрузка действия">
                    <Skeleton className="h-5 w-2/3" />
                    <Skeleton className="h-20 w-full" />
                </div>
            )}
            {query.isError && <ErrorMessage title="Не удалось открыть действие" message={errorText(query.error)} />}
            {action && status && Icon && (
                <section aria-label={`Действие: ${action.title}`} className="flex flex-col gap-3 text-[13px]">
                    <div>
                        <p className="text-[15px] font-semibold text-primary">{action.title}</p>
                        <p role="status" className={`mt-1 flex items-center gap-1.5 ${status.tone}`}>
                            <Icon size={14} aria-hidden="true" />
                            {status.label}
                        </p>
                    </div>
                    {pending && (
                        <p className="rounded-md border border-line bg-surface px-3 py-2 text-secondary">
                            Агент предлагает изменение от вашего имени. Оно выполнится только после вашего подтверждения; отменить потом можно только вручную.
                            Подтвердить можно до {formatDateTime(action.expires_at)}.
                        </p>
                    )}
                    {action.status === "expired" && (
                        <p className="text-secondary">
                            Действие не выполнено. Попросите агента предложить его заново — данные могли измениться. Предложение можно отклонить, чтобы закрыть его.
                        </p>
                    )}
                    {Object.keys(action.preview).length > 0 && (pending || action.status === "expired") && (
                        <div>
                            <h3 className="mb-1 font-semibold text-primary">Что затронет</h3>
                            <Parameters value={action.preview} />
                        </div>
                    )}
                    {typeof action.result.message === "string" && <p className="text-secondary">{action.result.message}</p>}
                    {typeof action.result.error === "string" && <p className="text-danger">{action.result.error}</p>}
                    <details open={pending}>
                        <summary className="cursor-pointer text-secondary" title="Точные параметры, с которыми выполнится действие; изменить их при подтверждении нельзя">
                            Параметры действия
                        </summary>
                        <div className="mt-2">
                            <Parameters value={action.arguments} />
                        </div>
                    </details>
                    <p className="text-[12px] text-muted">Предложено {formatDateTime(action.created_at)}</p>
                    {decisionError && <ErrorMessage title="Решение не принято" message={decisionError} />}
                </section>
            )}
        </Modal>
    );
}
