import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Rss } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { Popover } from "@/components/ui/Popover";
import { api, endpoints, queryKeys } from "@/lib/api";
import { formatDateTime } from "@/lib/dates";
import { copyText } from "@/lib/clipboard";
import { useToast } from "@/lib/toast";
import type { IcsTokenCreated, IcsTokenStatus } from "@/lib/types";

/**
 * «Подписаться в Outlook»: личная ссылка .ics на «Мой календарь». Ссылку сервер отдаёт
 * только при выпуске — показываем её один раз; дальше можно перевыпустить или отозвать.
 */
export function IcsSubscribePopover() {
    const queryClient = useQueryClient();
    const toast = useToast();
    const [isOpen, setOpen] = useState(false);
    const [createdUrl, setCreatedUrl] = useState<string | null>(null);
    const status = useQuery({
        queryKey: queryKeys.myCalendarIcsToken,
        queryFn: () => api.get<IcsTokenStatus>(endpoints.myCalendarIcsToken()),
        enabled: isOpen,
    });
    const issue = useMutation({
        mutationFn: () => api.post<IcsTokenCreated>(endpoints.myCalendarIcsToken()),
        onSuccess: (result) => {
            setCreatedUrl(result.url);
            queryClient.invalidateQueries({ queryKey: queryKeys.myCalendarIcsToken });
        },
        onError: (error) => toast.error(`Не удалось выпустить ссылку: ${(error as Error).message}`),
    });
    const revoke = useMutation({
        mutationFn: () => api.delete<void>(endpoints.myCalendarIcsToken()),
        onSuccess: () => {
            setCreatedUrl(null);
            queryClient.invalidateQueries({ queryKey: queryKeys.myCalendarIcsToken });
            toast.success("Ссылка отозвана — Outlook больше не получит обновления.");
        },
        onError: (error) => toast.error(`Не удалось отозвать ссылку: ${(error as Error).message}`),
    });

    async function copy(url: string) {
        try {
            await copyText(url);
            toast.success("Ссылка скопирована.");
        } catch {
            toast.error("Не удалось скопировать — выделите ссылку и скопируйте вручную.");
        }
    }

    const exists = status.data?.exists ?? false;
    const busy = issue.isPending || revoke.isPending;

    return (
        <Popover
            label="Подписка на календарь в Outlook"
            width={360}
            onOpenChange={(open) => {
                setOpen(open);
                if (!open) setCreatedUrl(null);
            }}
            trigger={
                <>
                    <Rss size={13} aria-hidden="true" />
                    <span>Подписаться в Outlook</span>
                </>
            }
        >
            <div className="flex flex-col gap-3 text-[12px]">
                <p className="text-secondary">
                    Личная ссылка: Outlook сам подтягивает сроки ваших задач и вехи. Это <b>только чтение</b> — изменения в
                    Outlook в трекер не вернутся. Ссылку не пересылайте: по ней видны названия ваших задач.
                </p>
                {createdUrl ? (
                    <div className="flex flex-col gap-2">
                        <p className="font-semibold text-primary">Ссылка показывается один раз — сохраните её сейчас.</p>
                        <div className="flex gap-1.5">
                            <Input readOnly value={createdUrl} aria-label="Ссылка на календарь" onFocus={(event) => event.currentTarget.select()} />
                            <Button size="sm" variant="primary" icon={<Copy size={13} aria-hidden="true" />} onClick={() => void copy(createdUrl)}>
                                Скопировать
                            </Button>
                        </div>
                        <p className="text-muted">Outlook: «Добавить календарь» → «Из Интернета» → вставьте ссылку.</p>
                    </div>
                ) : status.isPending ? (
                    <div role="status" aria-label="Загрузка подписки" className="flex flex-col gap-2"><Skeleton className="h-4 w-full" /><Skeleton className="h-4 w-3/4" /><Skeleton className="h-8 w-36" /></div>
                ) : status.error ? (
                    <ErrorMessage message={status.error.message} action={<Button onClick={() => void status.refetch()}>Повторить</Button>} />
                ) : exists ? (
                    <div className="flex flex-col gap-2">
                        <p className="text-secondary">
                            Ссылка уже выпущена{status.data?.created_at ? ` ${formatDateTime(status.data.created_at)}` : ""}.
                            {status.data?.last_used_at ? ` Последний раз календарь обновлялся ${formatDateTime(status.data.last_used_at)}.` : ""}
                        </p>
                        <div className="flex flex-wrap gap-1.5">
                            <Button size="sm" disabled={busy} onClick={() => issue.mutate()} title="Выпустить новую ссылку — старая сразу перестанет работать">
                                Перевыпустить
                            </Button>
                            <Button
                                size="sm"
                                variant="destructive"
                                disabled={busy}
                                onClick={() => {
                                    if (window.confirm("Отозвать ссылку? Календарь в Outlook перестанет обновляться.")) revoke.mutate();
                                }}
                            >
                                Отозвать
                            </Button>
                        </div>
                    </div>
                ) : (
                    <Button size="sm" variant="primary" disabled={busy} onClick={() => issue.mutate()}>
                        {issue.isPending ? "Выпускаем…" : "Создать ссылку"}
                    </Button>
                )}
            </div>
        </Popover>
    );
}
