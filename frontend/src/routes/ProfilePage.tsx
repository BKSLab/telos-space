import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { ChevronDown, KeyRound, ShieldCheck, Trash2, Upload, UserRound } from "lucide-react";
import { api, authEndpoints, queryKeys } from "@/lib/api";
import { formatFullDate } from "@/lib/dates";
import { fullName, type PasswordChangePayload, type User, type UserUpdatePayload } from "@/lib/types";
import { cn } from "@/lib/cn";
import { MIN_PASSWORD_LENGTH } from "@/lib/authForm";
import { useCurrentUser } from "@/lib/useAuth";
import { useToast } from "@/lib/toast";
import { Page } from "@/components/layout/AppShell";
import { InstallAppCard } from "@/components/layout/InstallAppCard";
import { Button, IconButton } from "@/components/ui/Button";
import { Section } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { UserAvatar } from "@/components/users/UserAvatar";
import { ApiKeysPanel } from "@/components/profile/ApiKeysPanel";
import { LegacyTokensPanel } from "@/components/profile/LegacyTokensPanel";

const MAX_AVATAR_BYTES = 5 * 1024 * 1024;
const AVATAR_TYPES = ["image/jpeg", "image/png", "image/webp"];

interface ProfileFormValues {
    lastName: string;
    firstName: string;
    middleName: string;
    email: string;
    phone: string;
    telegram: string;
}

function toFormValues(user: User): ProfileFormValues {
    return {
        lastName: user.last_name,
        firstName: user.first_name,
        middleName: user.middle_name ?? "",
        email: user.email ?? "",
        phone: user.phone ?? "",
        telegram: user.telegram ?? "",
    };
}

function toPayload(values: ProfileFormValues): UserUpdatePayload {
    return {
        last_name: values.lastName.trim(),
        first_name: values.firstName.trim(),
        middle_name: values.middleName.trim() || null,
        email: values.email.trim() || null,
        phone: values.phone.trim() || null,
        telegram: values.telegram.trim() || null,
    };
}

export function ProfilePage() {
    const { data: user, isPending, error, refetch } = useCurrentUser();

    if (error && !user) return <Page><h1 className="text-[24px] font-semibold text-primary">Профиль</h1><ErrorMessage message={(error as Error).message} action={<Button onClick={() => void refetch()}>Повторить загрузку</Button>} /></Page>;

    if (isPending || !user) {
        return (
            <Page className="max-w-3xl">
                <Skeleton className="h-64 w-full" />
            </Page>
        );
    }
    // key сбрасывает черновики формы, если пользователь сменился.
    return <ProfileForm key={user.id} user={user} />;
}

function ProfileForm({ user }: { user: User }) {
    const location = useLocation();
    const [params] = useSearchParams();
    const requestedTab = params.get("tab");
    const tab = requestedTab === "security" || requestedTab === "keys" ? requestedTab : "details";
    const [legacyOpen, setLegacyOpen] = useState(false);
    useEffect(() => {
        if (location.hash !== "#install" || tab !== "details") return;
        const frame = requestAnimationFrame(() => document.getElementById("install")?.scrollIntoView({ block: "start" }));
        return () => cancelAnimationFrame(frame);
    }, [location.hash, tab]);
    const queryClient = useQueryClient();
    const toast = useToast();
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [values, setValues] = useState<ProfileFormValues>(() => toFormValues(user));
    const [avatarVersion, setAvatarVersion] = useState(0);
    const [avatarError, setAvatarError] = useState<string | null>(null);
    const [passwords, setPasswords] = useState({ current: "", next: "", confirm: "" });

    const refreshUser = () => queryClient.invalidateQueries({ queryKey: queryKeys.currentUser });

    const saveMutation = useMutation({
        mutationFn: () => api.patch<User>(authEndpoints.profile(), toPayload(values)),
        onSuccess: (updated) => {
            queryClient.setQueryData(queryKeys.currentUser, updated);
            toast.success("Профиль сохранён");
        },
    });

    const passwordMutation = useMutation({
        mutationFn: (payload: PasswordChangePayload) =>
            api.post<void>(authEndpoints.password(), payload),
        onSuccess: () => {
            setPasswords({ current: "", next: "", confirm: "" });
            toast.success("Пароль изменён");
        },
    });

    const uploadMutation = useMutation({
        mutationFn: (file: File) => {
            const body = new FormData();
            body.append("file", file);
            return api.postForm<void>(authEndpoints.avatar(), body);
        },
        onSuccess: async () => {
            setAvatarVersion((version) => version + 1);
            await refreshUser();
        },
        onError: (error) => setAvatarError((error as Error).message),
    });

    const deleteAvatarMutation = useMutation({
        mutationFn: () => api.delete<void>(authEndpoints.avatar()),
        onSuccess: async () => {
            setAvatarVersion((version) => version + 1);
            await refreshUser();
        },
        onError: (error) => setAvatarError((error as Error).message),
    });

    function pickFile(file: File | undefined) {
        setAvatarError(null);
        if (file === undefined) {
            return;
        }
        // Проверяем до отправки: незачем гонять по сети файл, который сервер отвергнет.
        if (!AVATAR_TYPES.includes(file.type)) {
            setAvatarError("Подойдёт только JPEG, PNG или WebP.");
            return;
        }
        if (file.size > MAX_AVATAR_BYTES) {
            setAvatarError("Файл больше 5 МБ.");
            return;
        }
        uploadMutation.mutate(file);
    }

    const passwordMismatch =
        passwords.confirm !== "" && passwords.confirm !== passwords.next;
    const canChangePassword =
        passwords.current !== "" &&
        passwords.next.length >= MIN_PASSWORD_LENGTH &&
        !passwordMismatch &&
        passwords.confirm !== "" &&
        !passwordMutation.isPending;

    return (
        <Page className="max-w-[1400px]">
            <header className="flex flex-col gap-5">
                <div className="space-y-1"><h1 className="text-[24px] font-semibold tracking-[-0.03em] text-primary">Профиль</h1><p className="text-[13px] text-muted">Личные данные, безопасность и доступ ваших интеграций</p></div>
                <div className="surface-card flex min-w-0 items-center gap-4 rounded-[var(--radius-card)] border border-line p-4 sm:p-5">
                    <UserAvatar user={user} size="lg" version={avatarVersion} className="size-14 text-[18px] sm:size-16" />
                    <div className="min-w-0 space-y-1"><p className="break-words text-[18px] font-semibold text-primary">{fullName(user)}</p><p className="font-mono text-[12px] text-muted">{user.username}</p><p className="text-[12px] text-muted">В TELOS SPACE с {formatFullDate(user.created_at)}</p></div>
                </div>
            </header>
            <nav aria-label="Разделы профиля" className="-mt-1 flex border-b border-line">
                {([
                    { id: "details", label: "Личные данные", short: "Данные", icon: UserRound },
                    { id: "security", label: "Безопасность", short: "Безопасность", icon: ShieldCheck },
                    { id: "keys", label: "Ключи доступа", short: "Ключи", icon: KeyRound },
                ] as const).map(({ id, label, short, icon: Icon }) => <Link key={id} to={`/profile?tab=${id}`} aria-label={label} aria-current={tab === id ? "page" : undefined}
                    className={cn("brand-tab -mb-px flex min-h-11 min-w-0 flex-1 items-center justify-center gap-2 border-b px-2 py-3 text-[13px] font-medium focus-visible:outline-2 focus-visible:outline-accent sm:flex-none sm:px-4", tab === id ? "border-accent bg-accent/[0.075] text-primary" : "border-transparent text-muted hover:bg-hover hover:text-primary")}>
                    <Icon size={16} className="hidden shrink-0 sm:block" aria-hidden="true" /><span className="hidden sm:inline">{label}</span><span className="sm:hidden">{short}</span>
                </Link>)}
            </nav>

            {tab === "details" && <div className="grid min-w-0 items-start gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
            <div className="surface-card flex min-w-0 flex-col gap-5 rounded-[var(--radius-card)] border border-line p-4 sm:p-5">
            <Section title="Фотография">
                <div className="flex flex-wrap items-center gap-5 rounded-[var(--radius-card)] bg-surface/40 px-4 py-4">
                    <div className="flex min-w-0 flex-1 flex-col gap-2">
                        {avatarError && <ErrorMessage message={avatarError} />}
                        <p className="text-[12px] text-muted">JPEG, PNG или WebP, до 5 МБ.</p>
                        <div className="flex flex-wrap gap-2">
                            <input
                                ref={fileInputRef}
                                type="file"
                                accept={AVATAR_TYPES.join(",")}
                                className="hidden"
                                onChange={(event) => {
                                    pickFile(event.target.files?.[0]);
                                    event.target.value = "";
                                }}
                            />
                            <Button
                                icon={<Upload size={14} />}
                                disabled={uploadMutation.isPending}
                                onClick={() => fileInputRef.current?.click()}
                            >
                                {user.has_avatar ? "Заменить" : "Загрузить"}
                            </Button>
                            {user.has_avatar && (
                                <IconButton
                                    label="Удалить фотографию"
                                    variant="destructive"
                                    disabled={deleteAvatarMutation.isPending}
                                    onClick={() => deleteAvatarMutation.mutate()}
                                >
                                    <Trash2 size={14} aria-hidden="true" />
                                </IconButton>
                            )}
                        </div>
                    </div>
                </div>
            </Section>

            <Section title="О пользователе" className="border-t border-line-subtle pt-5">
                <div className="flex flex-col gap-5 px-1">
                    {saveMutation.error && (
                        <ErrorMessage message={(saveMutation.error as Error).message} />
                    )}

                    <div className="grid gap-4 sm:grid-cols-3">
                        <Field label="Фамилия">
                            {(id) => (
                                <Input
                                    id={id}
                                    value={values.lastName}
                                    onChange={(event) =>
                                        setValues({ ...values, lastName: event.target.value })
                                    }
                                />
                            )}
                        </Field>
                        <Field label="Имя">
                            {(id) => (
                                <Input
                                    id={id}
                                    value={values.firstName}
                                    onChange={(event) =>
                                        setValues({ ...values, firstName: event.target.value })
                                    }
                                />
                            )}
                        </Field>
                        <Field label="Отчество" hint="Необязательно">
                            {(id) => (
                                <Input
                                    id={id}
                                    value={values.middleName}
                                    onChange={(event) =>
                                        setValues({ ...values, middleName: event.target.value })
                                    }
                                />
                            )}
                        </Field>
                    </div>

                    <div className="grid gap-4 sm:grid-cols-3">
                        <Field label="Почта">
                            {(id) => (
                                <Input
                                    id={id}
                                    type="email"
                                    value={values.email}
                                    onChange={(event) =>
                                        setValues({ ...values, email: event.target.value })
                                    }
                                />
                            )}
                        </Field>
                        <Field label="Телефон">
                            {(id) => (
                                <Input
                                    id={id}
                                    value={values.phone}
                                    onChange={(event) =>
                                        setValues({ ...values, phone: event.target.value })
                                    }
                                />
                            )}
                        </Field>
                        <Field label="Telegram">
                            {(id) => (
                                <Input
                                    id={id}
                                    placeholder="@boris"
                                    value={values.telegram}
                                    onChange={(event) =>
                                        setValues({ ...values, telegram: event.target.value })
                                    }
                                />
                            )}
                        </Field>
                    </div>

                    <div className="flex justify-end pt-1">
                        <Button
                            variant="primary"
                            disabled={
                                values.lastName.trim() === "" ||
                                values.firstName.trim() === "" ||
                                saveMutation.isPending
                            }
                            onClick={() => saveMutation.mutate()}
                        >
                            Сохранить
                        </Button>
                    </div>
                </div>
            </Section>
            </div>
            <aside className="min-w-0"><InstallAppCard /></aside>
            </div>}

            {tab === "security" && <div className="surface-card max-w-4xl rounded-[var(--radius-card)] border border-line p-4 sm:p-5">
            <Section title="Смена пароля">
                <div className="flex flex-col gap-5 px-1">
                    {passwordMutation.error && (
                        <ErrorMessage message={(passwordMutation.error as Error).message} />
                    )}

                    <div className="grid gap-4 sm:grid-cols-3">
                        <Field label="Текущий пароль">
                            {(id) => (
                                <Input
                                    id={id}
                                    type="password"
                                    autoComplete="current-password"
                                    value={passwords.current}
                                    onChange={(event) =>
                                        setPasswords({ ...passwords, current: event.target.value })
                                    }
                                />
                            )}
                        </Field>
                        <Field label="Новый пароль" hint={`Не короче ${MIN_PASSWORD_LENGTH} символов`}>
                            {(id) => (
                                <Input
                                    id={id}
                                    type="password"
                                    autoComplete="new-password"
                                    value={passwords.next}
                                    onChange={(event) =>
                                        setPasswords({ ...passwords, next: event.target.value })
                                    }
                                />
                            )}
                        </Field>
                        <Field
                            label="Повторите пароль"
                            error={passwordMismatch ? "Пароли не совпадают." : undefined}
                        >
                            {(id) => (
                                <Input
                                    id={id}
                                    type="password"
                                    autoComplete="new-password"
                                    value={passwords.confirm}
                                    onChange={(event) =>
                                        setPasswords({ ...passwords, confirm: event.target.value })
                                    }
                                />
                            )}
                        </Field>
                    </div>

                    <div className="flex justify-end pt-1">
                        <Button
                            disabled={!canChangePassword}
                            onClick={() =>
                                passwordMutation.mutate({
                                    current_password: passwords.current,
                                    password: passwords.next,
                                    password_confirm: passwords.confirm,
                                })
                            }
                        >
                            Сменить пароль
                        </Button>
                    </div>
                </div>
            </Section>
            </div>}
            {tab === "keys" && <div className="flex min-w-0 flex-col gap-6">
                <div className="surface-card min-w-0 rounded-[var(--radius-card)] border border-line p-4 sm:p-5"><ApiKeysPanel /></div>
                <details className="group rounded-[var(--radius-card)] border border-line-subtle bg-surface/35" open={legacyOpen} onToggle={event => setLegacyOpen(event.currentTarget.open)}>
                    <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-3 rounded-md px-4 py-3 text-[13px] font-medium text-secondary focus-visible:outline-2 focus-visible:outline-accent">Прежние токены<ChevronDown size={16} aria-hidden="true" className="shrink-0 transition-transform group-open:rotate-180" /></summary>
                    {legacyOpen && <div className="border-t border-line-subtle p-4 sm:p-5"><LegacyTokensPanel /></div>}
                </details>
                <p className="text-[12px] leading-6 text-muted">Подключение клиентов: <Link to="/help/api" className="text-accent hover:text-accent-hover">справка по API</Link> · <Link to="/help/mcp" className="text-accent hover:text-accent-hover">справка по MCP</Link></p>
            </div>}
        </Page>
    );
}
