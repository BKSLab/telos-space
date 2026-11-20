import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check, ChevronRight, Copy, ExternalLink, ShieldCheck, Webhook } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { api, ApiError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { contextApi, type GitConnection } from "@/lib/projectKnowledge";

const docs = "https://docs.github.com/en/webhooks/using-webhooks/creating-webhooks";
const linkClass = "text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent focus-visible:outline-2 focus-visible:outline-accent";

function webhookUrl(connection: Pick<GitConnection, "id" | "project_id">): string | null {
    const base = import.meta.env.VITE_PUBLIC_URL || (!import.meta.env.DEV ? window.location.origin : "");
    if (!base) return null;
    try {
        const url = new URL(base);
        if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
        return new URL(`${contextApi.git(connection.project_id)}/connections/${connection.id}/events`, url.origin).href;
    } catch { return null; }
}

export function GitWebhookSettings({ connection, refresh }: { connection: GitConnection; refresh: () => Promise<unknown> }) {
    const [editing, setEditing] = useState(false);
    const [secret, setSecret] = useState("");
    const [notice, setNotice] = useState("");
    const url = webhookUrl(connection);
    const secure = url?.startsWith("https://");
    const mutation = useMutation({
        mutationFn: (action: "save" | "disable") => api.put(`${contextApi.git(connection.project_id)}/connections/${connection.id}/webhook-secret`, {
            expected_revision: connection.revision, webhook_secret: action === "save" ? secret : null,
        }),
        onSuccess: async (_, action) => {
            setSecret(""); setEditing(false);
            setNotice(action === "disable" ? "Приём webhook отключён в TELOS. Удалите или отключите webhook в GitHub." : "Секрет сохранён в TELOS. Укажите тот же секрет в GitHub и проверьте доставку.");
            await refresh();
        },
    });
    async function copy(value: string, message: string) {
        try { await copyText(value); setNotice(message); }
        catch { setNotice("Не удалось скопировать. Выделите и скопируйте значение вручную."); }
    }
    return <details className="group/webhook border-t border-line-subtle pt-1">
        <summary className="touch-target flex cursor-pointer list-none items-center gap-2 text-[13px] font-medium text-secondary focus-visible:outline-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
            <Webhook size={15} aria-hidden="true" />Webhook GitHub<ChevronRight size={14} className="ml-auto shrink-0 group-open/webhook:rotate-90" aria-hidden="true" />
        </summary>
        <div className="space-y-4 pb-1 pt-2 text-[13px] text-secondary">
            <p>GitHub сообщает об изменениях сразу. Резервная сверка продолжает работать по расписанию.</p>
            {!secure && <p role="status" className="rounded-md border border-warning/25 bg-warning/10 p-3 text-warning">
                {url ? "Сейчас адрес приёмника использует HTTP. Перед подключением webhook настройте доступный GitHub HTTPS-адрес с действующим сертификатом." : "Публичный адрес приёмника не задан. Для подключения webhook нужен доступный GitHub HTTPS-адрес сервиса."}
                {" "}Сохранение секрета само по себе не подключает GitHub.
            </p>}
            {url && <div className="space-y-2">
                <Field label="Payload URL">{id => <Input id={id} value={url} readOnly onFocus={event => event.currentTarget.select()} />}</Field>
                <Button size="sm" icon={<Copy size={14} aria-hidden="true" />} onClick={() => void copy(url, "Адрес приёмника скопирован.")}>Копировать адрес</Button>
            </div>}
            {connection.has_webhook_secret && !editing ? <div className="space-y-2">
                <p className="flex items-center gap-2 text-success"><ShieldCheck size={16} aria-hidden="true" />Секрет сохранён в TELOS</p>
                <p className="text-[12px] text-muted">Сохранённый секрет не отображается. При замене обновите Secret в GitHub.</p>
                <div className="flex flex-wrap gap-2"><Button size="sm" disabled={mutation.isPending} onClick={() => { mutation.reset(); setNotice(""); setEditing(true); }}>Заменить секрет</Button><Button size="sm" disabled={mutation.isPending} onClick={() => mutation.mutate("disable")}>Отключить приём webhook</Button></div>
            </div> : <form className="space-y-3" onSubmit={event => { event.preventDefault(); if (secret.trim()) mutation.mutate("save"); }}>
                <Field label="Новый секрет webhook" hint="Это отдельный случайный секрет, не токен GitHub. Скопируйте его в Secret на GitHub до сохранения здесь.">{id => <Input id={id} type="password" autoComplete="new-password" value={secret} required maxLength={1024} disabled={mutation.isPending} onChange={event => setSecret(event.target.value)} />}</Field>
                <div className="flex flex-wrap gap-2">
                    <Button size="sm" disabled={mutation.isPending} onClick={() => { const bytes = crypto.getRandomValues(new Uint8Array(32)); setSecret(Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("")); setNotice("Секрет создан. Скопируйте его в GitHub, затем сохраните в TELOS."); }}>Создать секрет</Button>
                    <Button size="sm" icon={<Copy size={14} aria-hidden="true" />} disabled={!secret || mutation.isPending} onClick={() => void copy(secret, "Новый секрет скопирован. Вставьте его в Secret на GitHub.")}>Копировать секрет</Button>
                    <Button type="submit" size="sm" variant="primary" disabled={!secret.trim() || mutation.isPending}>Сохранить секрет</Button>
                    {editing && <Button size="sm" disabled={mutation.isPending} onClick={() => { setEditing(false); setSecret(""); mutation.reset(); }}>Отменить</Button>}
                </div>
            </form>}
            {notice && <p role="status" className="text-[12px] text-secondary">{notice}</p>}
            {mutation.error && <ErrorMessage message={mutation.error.message} />}
            {mutation.error instanceof ApiError && mutation.error.status === 409 && <Button size="sm" onClick={async () => { await refresh(); mutation.reset(); setSecret(""); setEditing(false); }}>Загрузить актуальные настройки webhook</Button>}
            <details className="group/instructions border-t border-line-subtle">
                <summary className="touch-target flex cursor-pointer list-none items-center gap-2 font-medium text-accent focus-visible:outline-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden"><ChevronRight size={14} aria-hidden="true" className="group-open/instructions:rotate-90" />Как подключить webhook в GitHub</summary>
                <div className="space-y-3 pt-2 leading-relaxed">
                    <p>Нужны права владельца или администратора репозитория в GitHub. Расширять права токена чтения в профиле TELOS для этой настройки не требуется.</p>
                    <ol className="list-decimal space-y-2 pl-5 marker:text-accent">
                        <li>Откройте репозиторий → <strong>Settings → Webhooks → Add webhook</strong>.</li>
                        <li>В <strong>Payload URL</strong> вставьте HTTPS-адрес выше. Выберите <strong>Content type: application/json</strong>.</li>
                        <li>Создайте секрет здесь, скопируйте его в <strong>Secret</strong> на GitHub и нажмите «Сохранить секрет» в TELOS.</li>
                        <li>Выберите <strong>Let me select individual events</strong> и отметьте <strong>Pushes</strong> (<code>push</code>). Для связей задач, CI и выкладок также отметьте <strong>Pull requests, Check runs, Check suites, Statuses, Deployments, Deployment statuses</strong>.</li>
                        <li>Оставьте SSL-проверку включённой, отметьте <strong>Active</strong> и нажмите <strong>Add webhook</strong>.</li>
                        <li>GitHub отправит <code>ping</code>. Затем сделайте обычный push в выбранную ветку <code className="break-all">{connection.ref}</code> и проверьте доставку в GitHub и версию индекса в TELOS.</li>
                    </ol>
                    <p className="flex items-start gap-2 text-[12px] text-muted"><Check size={15} className="mt-0.5 shrink-0" aria-hidden="true" />Полученный ping подтверждает доставку, но не обновление кода. Дождитесь успешной сверки события и нужной версии индекса.</p>
                    <div className="flex flex-wrap gap-x-4 gap-y-2 text-[12px]"><a href={docs} target="_blank" rel="noopener noreferrer" className={linkClass}>Инструкция GitHub <ExternalLink size={12} className="inline" aria-hidden="true" /></a><a href="https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks" target="_blank" rel="noopener noreferrer" className={linkClass}>HTTPS и защита webhook</a></div>
                </div>
            </details>
        </div>
    </details>;
}
