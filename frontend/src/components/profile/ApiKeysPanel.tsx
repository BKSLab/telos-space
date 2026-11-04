import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound } from "lucide-react";
import {
    createPersonalApiKey,
    endOfDayIso,
    expiryState,
    fetchPersonalApiKeys,
    fetchPersonalApiScopes,
    isoToDateInput,
    keyStatus,
    personalApiKeyQueryKeys,
    updatePersonalApiKey,
    type ApiKey,
    type ApiKeyCreated,
    type ApiScope,
} from "@/lib/apiKeys";
import { formatDateTime, formatFullDate } from "@/lib/dates";
import { Button, LinkButton } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { HelpHint } from "@/components/ui/HelpHint";
import { Modal } from "@/components/ui/Modal";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";

/** Права по умолчанию при выпуске — только чтение: запись включается осознанно. */
const DEFAULT_SCOPES = ["projects:read", "tasks:read"];

const STATUS_LABEL = { active: "Действует", revoked: "Отозван", expired: "Истёк" } as const;
const STATUS_CLASS = { active: "border-success/25 bg-success/10 text-success", revoked: "border-line-subtle bg-control text-secondary", expired: "border-warning/25 bg-warning/10 text-warning" } as const;
const TONE_CLASS = { ok: "text-secondary", muted: "text-secondary", warn: "text-warning", danger: "text-danger" } as const;

function ScopeChips({ scopes, descriptions }: { scopes: string[]; descriptions: Map<string, string> }) {
    if (scopes.length === 0) return <span className="text-[11px] text-danger">прав нет</span>;
    return (
        <span className="flex flex-wrap gap-1">
            {scopes.map((scope) => (
                <code key={scope} className="inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] border-line-subtle bg-control text-secondary font-mono" title={descriptions.get(scope) ?? scope}>
                    {scope}
                </code>
            ))}
        </span>
    );
}

function ScopeCheckboxes({ scopes, value, onChange }: { scopes: ApiScope[]; value: string[]; onChange: (next: string[]) => void }) {
    return (
        <fieldset className="flex flex-col gap-2">
            <legend className="text-[11px] font-medium text-secondary mb-1.5 flex items-center gap-1">
                Права
                <HelpHint label="Что значат права ключа" title="Каждое право открывает группу методов API и инструментов MCP">
                    Каждое право открывает группу методов REST API и инструментов MCP в пределах вашего доступа к проектам, задачам и личным доскам. Право agent:ask разрешает обращаться к помощнику от вашего имени.
                </HelpHint>
            </legend>
            {scopes.length === 0 && <p className="text-xs text-muted">Список прав не загрузился — обновите страницу.</p>}
            {scopes.map((scope) => (
                <label key={scope.code} className="flex items-start gap-2 text-[12px] text-primary">
                    <input
                        type="checkbox"
                        className="mt-0.5"
                        checked={value.includes(scope.code)}
                        onChange={(event) => onChange(event.target.checked ? [...value, scope.code] : value.filter((item) => item !== scope.code))}
                    />
                    <span>
                        <code className="inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] border-line-subtle bg-control text-secondary font-mono">{scope.code}</code>
                        <span className="ml-1 text-secondary">{scope.description}</span>
                    </span>
                </label>
            ))}
        </fieldset>
    );
}

interface KeyFormState {
    kind: "personal" | "service";
    label: string;
    scopes: string[];
    expires: string;
}

/** Диалог выпуска и правки личного ключа: владелец — сам пользователь, не выбирается. */
function KeyFormDialog({
    mode,
    initial,
    scopes,
    isPending,
    error,
    onSubmit,
    onClose,
}: {
    mode: "create" | "edit";
    initial: KeyFormState;
    scopes: ApiScope[];
    isPending: boolean;
    error: string;
    onSubmit: (form: KeyFormState) => void;
    onClose: () => void;
}) {
    const [form, setForm] = useState<KeyFormState>(initial);
    const [localError, setLocalError] = useState("");

    function submit(event: React.FormEvent) {
        event.preventDefault();
        if (isPending) return;
        if (!form.label.trim()) return setLocalError("Укажите название ключа — по нему его узнают в списке.");
        if (form.scopes.length === 0) return setLocalError("Выберите хотя бы одно право: ключ без прав не открывает ни одного метода.");
        setLocalError("");
        onSubmit(form);
    }

    const shownError = localError || error;
    return (
        <Modal
            title={mode === "create" ? "Выпустить API-ключ" : "Изменить API-ключ"}
            description={mode === "create" ? "Ключ для ваших скриптов и клиентов: работает от вашего имени в пределах отмеченных прав." : undefined}
            isOpen
            onOpenChange={(open) => !open && !isPending && onClose()}
            isDismissable={!isPending}
            size="md"
        >
            <form id="personal-key-form" className="flex flex-col gap-4" onSubmit={submit}>
                {mode === "create" && <Field label="Тип ключа" hint="Оба типа работают с вашим доступом.">
                    {(id) => <Select id={id} value={form.kind} disabled={isPending}
                        onChange={(event) => setForm({...form, kind: event.target.value as "personal" | "service"})}>
                        <option value="personal">Личный</option>
                        <option value="service">Сервисный</option>
                    </Select>}
                </Field>}
                <Field label="Название" hint="Где будет использоваться ключ: «Скрипт сверки», «Claude на ноутбуке».">
                    {(id) => (
                        <Input
                            id={id}
                            value={form.label}
                            maxLength={160}
                            placeholder="Скрипт сверки задач"
                            title="По названию ключ узнают в списке и в журнале обращений"
                            onChange={(event) => setForm({ ...form, label: event.target.value })}
                        />
                    )}
                </Field>
                <ScopeCheckboxes scopes={scopes} value={form.scopes} onChange={(next) => setForm({ ...form, scopes: next })} />
                <Field label="Действует до" hint="Пусто — ключ бессрочный. Для разовой работы поставьте дату: просроченный ключ закроется сам.">
                    {(id) => (
                        <Input
                            id={id}
                            type="date"
                            value={form.expires}
                            title="После этой даты ключ перестанет приниматься. Пусто — бессрочный"
                            onChange={(event) => setForm({ ...form, expires: event.target.value })}
                        />
                    )}
                </Field>
                {shownError && (
                    <p className="rounded-[var(--radius-card)] border border-danger/30 bg-danger/10 text-danger px-3 py-2 text-[12px]" role="alert">
                        {shownError}
                    </p>
                )}
            </form>
            <div className="mt-4 flex justify-end gap-2">
                <Button disabled={isPending} onClick={onClose}>Отмена</Button>
                <Button variant="primary" type="submit" form="personal-key-form" disabled={isPending} title={mode === "create" ? "Выпустить ключ: секрет покажем один раз" : "Сохранить название, права и срок"}>
                    {mode === "create" ? "Выпустить" : "Сохранить"}
                </Button>
            </div>
        </Modal>
    );
}

function CopySecretButton({ value }: { value: string }) {
    const [copied, setCopied] = useState(false);
    const [failed, setFailed] = useState(false);
    async function copy() {
        try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            setFailed(false);
            window.setTimeout(() => setCopied(false), 2000);
        } catch {
            setFailed(true);
        }
    }
    return (
        <span className="flex flex-col items-end gap-1">
            <Button onClick={() => void copy()} title="Скопировать ключ в буфер обмена" icon={copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}>
                {copied ? "Скопировано" : "Копировать"}
            </Button>
            {failed && <span className="text-xs text-danger">Буфер обмена недоступен — выделите ключ и скопируйте вручную</span>}
        </span>
    );
}

/** Секрет — один раз: закрыли окно, и показать его снова нельзя (хранится только хеш). */
function SecretDialog({ created, onClose }: { created: ApiKeyCreated; onClose: () => void }) {
    return (
        <Modal title="Ключ выпущен" isOpen onOpenChange={(open) => !open && onClose()} isDismissable={false} size="md">
            <div className="flex flex-col gap-4">
                <div className="rounded-[var(--radius-card)] border border-warning/30 bg-warning/10 text-warning flex-col gap-0.5 px-3 py-2" role="note">
                    <strong className="text-[13px]">Сохраните ключ сейчас</strong>
                    <span className="text-[12px]">Трекер хранит только хеш: показать ключ ещё раз он не сможет. Потеряли — выпустите новый и отзовите этот.</span>
                </div>
                <div className="flex flex-col gap-2">
                    <span className="text-[11px] font-medium text-secondary">Ключ «{created.label}»</span>
                    <code data-testid="api-key-secret" className="font-mono select-all break-all rounded-[var(--radius-panel)] bg-surface-2 px-3 py-2.5 text-[12px] text-primary">
                        {created.raw_key}
                    </code>
                    <div className="flex justify-end">
                        <CopySecretButton value={created.raw_key} />
                    </div>
                    <p className="text-xs text-muted">Передавайте ключ в заголовке <code className="font-mono">Authorization: Bearer</code> — он даёт ваши права доступа, так что берегите его как пароль.</p>
                </div>
                <div className="flex justify-end">
                    <Button variant="primary" onClick={onClose} title="Закрыть окно — ключ больше не покажем">
                        Я сохранил ключ
                    </Button>
                </div>
            </div>
        </Modal>
    );
}

function RevokeDialog({ apiKey, isPending, onConfirm, onClose }: { apiKey: ApiKey; isPending: boolean; onConfirm: () => void; onClose: () => void }) {
    return (
        <Modal title="Отозвать ключ?" isOpen onOpenChange={(open) => !open && onClose()}>
            <p className="text-[13px] text-primary">
                Ключ «{apiKey.label}» перестанет работать сразу: скрипт или клиент, который им пользуется, получит 401. Для нового подключения можно выпустить другой ключ.
            </p>
            <div className="mt-4 flex justify-end gap-2">
                <Button disabled={isPending} onClick={onClose}>Отмена</Button>
                <Button variant="destructive" onClick={onConfirm} disabled={isPending} title="Отозвать ключ">
                    Отозвать
                </Button>
            </div>
        </Modal>
    );
}

/** Раздел «Мои API-ключи»: ключи локального пользователя, доступен каждому. */
export function ApiKeysPanel() {
    const queryClient = useQueryClient();
    const [formMode, setFormMode] = useState<{ mode: "create" } | { mode: "edit"; key: ApiKey } | null>(null);
    const [formError, setFormError] = useState("");
    const [created, setCreated] = useState<ApiKeyCreated | null>(null);
    const [revoking, setRevoking] = useState<ApiKey | null>(null);
    const [actionError, setActionError] = useState("");

    const keysQuery = useQuery({ queryKey: personalApiKeyQueryKeys.list, queryFn: fetchPersonalApiKeys });
    const scopesQuery = useQuery({ queryKey: personalApiKeyQueryKeys.scopes, queryFn: fetchPersonalApiScopes, staleTime: Infinity });
    const scopes = scopesQuery.data ?? [];
    const descriptions = new Map(scopes.map((scope) => [scope.code, scope.description]));

    const invalidate = () => void queryClient.invalidateQueries({ queryKey: personalApiKeyQueryKeys.list });

    const createMutation = useMutation({
        mutationFn: createPersonalApiKey,
        onSuccess: (data) => {
            invalidate();
            setFormMode(null);
            setCreated(data);
        },
        onError: (error: Error) => setFormError(error.message || "Не удалось выпустить ключ"),
    });
    const updateMutation = useMutation({
        mutationFn: ({ id, payload }: { id: string; payload: Parameters<typeof updatePersonalApiKey>[1] }) => updatePersonalApiKey(id, payload),
        onSuccess: () => {
            invalidate();
            setFormMode(null);
        },
        onError: (error: Error) => setFormError(error.message || "Не удалось сохранить ключ"),
    });
    const revokeMutation = useMutation({
        mutationFn: (key: ApiKey) => updatePersonalApiKey(key.id, { is_active: false }),
        onSuccess: () => {
            setActionError("");
            setRevoking(null);
            invalidate();
        },
        onError: (error: Error) => {
            setRevoking(null);
            setActionError(`Ключ не отозван: ${error.message}`);
        },
    });

    function openCreate() {
        setFormError("");
        setFormMode({ mode: "create" });
    }

    const keys = keysQuery.data ?? [];
    return (
        <section aria-label="API-ключи" className="flex min-w-0 flex-col gap-4">
            <header className="flex flex-wrap items-end justify-between gap-3">
                <div className="flex flex-col gap-1">
                    <h2 className="text-[18px] font-semibold tracking-[-0.03em] text-primary">API-ключи</h2>
                    <p className="text-[13px] text-muted">Доступ ваших клиентов и интеграций к трекеру</p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <LinkButton to="/help/api" variant="ghost">Справка по API</LinkButton>
                    <Button variant="primary" onClick={openCreate} icon={<KeyRound size={14} aria-hidden="true" />}>Выпустить ключ</Button>
                </div>
            </header>
            <p className="text-[13px] text-secondary">
                Ключи работают от вашего имени и получают только отмеченные права. Личный ключ подходит для вашего клиента, сервисный — для вашей интеграции или фонового скрипта.
            </p>
            {actionError && (
                <p className="rounded-[var(--radius-card)] border border-danger/30 bg-danger/10 text-danger px-3 py-2 text-[12px]" role="alert">
                    {actionError}
                </p>
            )}
            {scopesQuery.isError && <ErrorMessage title="Не удалось загрузить права" message={(scopesQuery.error as Error).message} action={<Button size="sm" onClick={() => void scopesQuery.refetch()}>Повторить</Button>} />}
            {keysQuery.isPending && <Skeleton className="h-40 w-full" />}
            {keysQuery.isError && <ErrorMessage title="Не удалось загрузить ключи" message={(keysQuery.error as Error).message} />}
            {keysQuery.data && keys.length === 0 && (
                <EmptyState
                    icon={<KeyRound className="size-5" />}
                    title="API-ключей пока нет"
                    description="Нажмите «Выпустить ключ», чтобы подключить свой скрипт или ИИ-клиент к задачам."
                />
            )}
            {keys.length > 0 && (
                <div className="rounded-[var(--radius-panel)] border border-line-subtle bg-surface overflow-x-auto p-0">
                    <table className="api-keys-table w-full text-left text-[13px] [&_th]:px-4 [&_th]:py-3 [&_th]:text-[11px] [&_th]:font-medium [&_th]:text-muted [&_td]:px-4 [&_td]:py-3 [&_tr]:border-b [&_tr]:border-line-subtle" aria-label="Мои API-ключи">
                        <thead>
                            <tr>
                                <th>Ключ</th>
                                <th>Права</th>
                                <th>Срок</th>
                                <th>Статус</th>
                                <th>
                                    <span className="sr-only">Действия</span>
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            {keys.map((key) => {
                                const status = keyStatus(key);
                                const expiry = expiryState(key);
                                return (
                                    <tr key={key.id} data-testid={`api-key-${key.id}`}>
                                        <td data-label="Ключ">
                                            <div className="text-[13px] font-medium text-primary">{key.label}</div>
                                            <span className="mr-2 text-[11px] text-muted">{key.kind === "service" ? "Сервисный" : "Личный"}</span>
                                            <code className="font-mono text-[11px] text-secondary">{key.key_prefix}…</code>
                                        </td>
                                        <td data-label="Права" className="min-w-0 break-words">
                                            <ScopeChips scopes={key.scopes} descriptions={descriptions} />
                                        </td>
                                        <td data-label="Срок" className={`text-[12px] ${TONE_CLASS[expiry.tone]}`}>{expiry.label}</td>
                                        <td data-label="Статус">
                                            <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] ${STATUS_CLASS[status]}`}>{STATUS_LABEL[status]}</span>
                                            {status === "revoked" ? (
                                                <div className="mt-0.5 text-[11px] text-secondary">
                                                    {key.revoked_at ? formatFullDate(key.revoked_at) : "дата неизвестна"}
                                                </div>
                                            ) : (
                                                <div className="mt-0.5 text-[11px] text-secondary" title="Когда ключ последний раз обращался к трекеру">
                                                    {key.last_used_at ? `вызов ${formatDateTime(key.last_used_at)}` : "не обращался"}
                                                </div>
                                            )}
                                        </td>
                                        <td>
                                            {status !== "revoked" && (
                                                <div className="flex items-center justify-end gap-1">
                                                    <Button
                                                        size="sm"
                                                        variant="ghost"
                                                        title="Изменить название, права или срок ключа"
                                                        onClick={() => {
                                                            setFormError("");
                                                            setFormMode({ mode: "edit", key });
                                                        }}
                                                    >
                                                        Изменить
                                                    </Button>
                                                    <Button size="sm" variant="destructive" onClick={() => {
                                                            setActionError("");
                                                            setRevoking(key);
                                                        }}
                                                        title="Отозвать ключ: перестанет работать сразу">
                                                        Отозвать
                                                    </Button>
                                                </div>
                                            )}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}

            {formMode?.mode === "create" && (
                <KeyFormDialog
                    mode="create"
                    initial={{ kind: "personal", label: "", scopes: DEFAULT_SCOPES.filter((code) => scopes.length === 0 || descriptions.has(code)), expires: "" }}
                    scopes={scopes}
                    isPending={createMutation.isPending}
                    error={formError}
                    onClose={() => setFormMode(null)}
                    onSubmit={(form) =>
                        createMutation.mutate({
                            kind: form.kind,
                            label: form.label.trim(),
                            scopes: form.scopes,
                            expires_at: endOfDayIso(form.expires),
                        })
                    }
                />
            )}
            {formMode?.mode === "edit" && (
                <KeyFormDialog
                    mode="edit"
                    initial={{ kind: formMode.key.kind, label: formMode.key.label, scopes: formMode.key.scopes, expires: isoToDateInput(formMode.key.expires_at) }}
                    scopes={scopes}
                    isPending={updateMutation.isPending}
                    error={formError}
                    onClose={() => setFormMode(null)}
                    onSubmit={(form) =>
                        updateMutation.mutate({
                            id: formMode.key.id,
                            payload: { label: form.label.trim(), scopes: form.scopes, expires_at: endOfDayIso(form.expires) },
                        })
                    }
                />
            )}
            {created && <SecretDialog created={created} onClose={() => setCreated(null)} />}
            {revoking && <RevokeDialog apiKey={revoking} isPending={revokeMutation.isPending} onConfirm={() => revokeMutation.mutate(revoking)} onClose={() => setRevoking(null)} />}
        </section>
    );
}
