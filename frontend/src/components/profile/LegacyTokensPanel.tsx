import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound } from "lucide-react";
import { api, authEndpoints, queryKeys } from "@/lib/api";
import { formatFullDate } from "@/lib/dates";
import { copyText } from "@/lib/clipboard";
import { scopeLabel, tokenState } from "@/lib/mcpConfig";
import type { ApiToken, ApiTokenCreated, ApiTokenCreatePayload, ApiTokenScope } from "@/lib/types";
import { useToast } from "@/lib/toast";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";

/** Existing READ/WRITE credentials keep their original API and permissions. */
export function LegacyTokensPanel() {
    const client = useQueryClient();
    const toast = useToast();
    const [name, setName] = useState("");
    const [scope, setScope] = useState<ApiTokenScope>("READ");
    const [ttl, setTtl] = useState("90");
    const [issued, setIssued] = useState<ApiTokenCreated | null>(null);
    const [revoking, setRevoking] = useState<ApiToken | null>(null);
    const query = useQuery({ queryKey: queryKeys.apiTokens, queryFn: () => api.get<ApiToken[]>(authEndpoints.apiTokens()) });
    const invalidate = () => void client.invalidateQueries({ queryKey: queryKeys.apiTokens });
    const create = useMutation({
        mutationFn: (payload: ApiTokenCreatePayload) => api.post<ApiTokenCreated>(authEndpoints.apiTokens(), payload),
        onSuccess: token => { setIssued(token); setName(""); invalidate(); },
    });
    const revoke = useMutation({
        mutationFn: (id: number) => api.delete<void>(authEndpoints.apiToken(id)),
        onSuccess: () => { setRevoking(null); invalidate(); toast.success("Токен отозван"); },
    });
    const copy = async () => {
        if (!issued) return;
        try { await copyText(issued.secret); toast.success("Токен скопирован"); }
        catch { toast.error("Буфер обмена недоступен — выделите и скопируйте токен вручную"); }
    };

    return <section aria-label="Прежние токены" className="min-w-0 space-y-5">
        <p className="text-[13px] leading-6 text-muted">READ/WRITE-токены для существующих клиентов. Для новых подключений используйте API-ключи с выбранными правами выше.</p>
        <form onSubmit={event => { event.preventDefault(); if (name.trim() && !create.isPending) create.mutate({ name: name.trim(), scope, ttl_days: ttl ? Number(ttl) : null }); }} className="grid items-end gap-3 sm:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_180px_150px_auto]">
            <Field label="Название токена">{id => <Input id={id} value={name} onChange={event => setName(event.target.value)} maxLength={100} required placeholder="Существующий клиент" />}</Field>
            <Field label="Права токена">{id => <Select id={id} value={scope} onChange={event => setScope(event.target.value as ApiTokenScope)}><option value="READ">Только чтение</option><option value="WRITE">Чтение и запись</option></Select>}</Field>
            <Field label="Срок токена">{id => <Select id={id} value={ttl} onChange={event => setTtl(event.target.value)}><option value="30">30 дней</option><option value="90">90 дней</option><option value="365">Год</option><option value="">Без срока</option></Select>}</Field>
            <Button type="submit" disabled={create.isPending || !name.trim()} icon={<KeyRound size={15} />}>Выпустить токен</Button>
        </form>
        {create.error && <ErrorMessage message={(create.error as Error).message} />}
        {query.isPending && <Skeleton className="h-24" />}
        {query.error && <ErrorMessage message={(query.error as Error).message} action={<Button onClick={() => void query.refetch()}>Повторить</Button>} />}
        {query.data?.length === 0 && <EmptyState title="Прежних токенов нет" />}
        <div className="divide-y divide-line-subtle">{query.data?.map(token => <div key={token.id} className="flex min-w-0 flex-wrap items-center justify-between gap-3 py-3">
            <div className="min-w-0 space-y-1">
                <p className="break-words text-[13px] font-medium text-primary">{token.name} <code className="text-[11px] text-muted">{token.prefix}…</code></p>
                <p className="text-[12px] leading-6 text-muted">{scopeLabel(token.scope)} · {tokenState(token)} · создан {formatFullDate(token.created_at)}</p>
                <p className="text-[12px] text-muted">{token.expires_at ? `До ${formatFullDate(token.expires_at)}` : "Без срока"}{token.last_used_at && ` · использован ${formatFullDate(token.last_used_at)}`}</p>
            </div>
            {token.revoked_at === null && <Button size="sm" variant="destructive" aria-label={`Отозвать токен ${token.name}`} onClick={() => { revoke.reset(); setRevoking(token); }}>Отозвать</Button>}
        </div>)}</div>
        {issued && <Modal title="Токен выпущен" isOpen onOpenChange={open => { if (!open) setIssued(null); }}>
            <div className="space-y-4"><p className="text-[13px] leading-6 text-secondary">Сохраните токен сейчас. После закрытия показать секрет повторно нельзя.</p>
                <code className="block rounded-md bg-surface-2 p-3 text-[12px] break-all text-primary">{issued.secret}</code>
                <div className="flex flex-wrap justify-end gap-2"><Button onClick={() => void copy()} icon={<Copy size={14} />}>Копировать токен</Button><Button variant="primary" onClick={() => setIssued(null)}>Я сохранил токен</Button></div>
            </div>
        </Modal>}
        {revoking && <Modal title="Отозвать токен?" isOpen isDismissable={!revoke.isPending} onOpenChange={open => { if (!open && !revoke.isPending) setRevoking(null); }}>
            <p className="text-[13px] leading-6 text-secondary">Токен «{revoking.name}» перестанет работать сразу. Использующие его клиенты потеряют доступ.</p>
            {revoke.error && <ErrorMessage className="mt-3" message={(revoke.error as Error).message} />}
            <div className="mt-4 flex justify-end gap-2"><Button disabled={revoke.isPending} onClick={() => setRevoking(null)}>Отмена</Button><Button variant="destructive" disabled={revoke.isPending} onClick={() => revoke.mutate(revoking.id)}>Отозвать</Button></div>
        </Modal>}
    </section>;
}
