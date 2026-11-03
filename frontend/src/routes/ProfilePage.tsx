import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2, Upload } from "lucide-react";
import { api, authEndpoints, queryKeys } from "@/lib/api";
import { formatFullDate } from "@/lib/dates";
import type { PasswordChangePayload, User, UserUpdatePayload } from "@/lib/types";
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
    const { data: user, isPending } = useCurrentUser();

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
        <Page className="max-w-3xl">
            <header className="flex flex-col gap-0.5">
                <h1 className="text-[22px] font-semibold tracking-[-0.03em] text-primary">
                    Профиль
                </h1>
                <p className="text-[13px] text-muted">
                    Учётная запись <span className="font-mono">{user.username}</span> создана{" "}
                    {formatFullDate(user.created_at)}
                </p>
            </header>

            <InstallAppCard />

            <Section title="Фотография">
                <div className="flex flex-wrap items-center gap-5 rounded-[var(--radius-card)] bg-surface/40 px-4 py-4">
                    <UserAvatar user={user} size="lg" version={avatarVersion} />
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

            <Section title="Смена пароля" className="border-t border-line-subtle pt-5">
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
        </Page>
    );
}
