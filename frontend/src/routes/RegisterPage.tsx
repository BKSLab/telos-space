import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { UserPlus } from "lucide-react";
import {
    EMPTY_REGISTER_FORM,
    isRegisterFormValid,
    toRegisterPayload,
    validateRegisterForm,
    type RegisterFormValues,
} from "@/lib/authForm";
import { useRegister } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { useMediaQuery } from "@/lib/useMediaQuery";

export function RegisterPage() {
    const navigate = useNavigate();
    const register = useRegister();
    const touch = useMediaQuery("(pointer: coarse)");
    const [values, setValues] = useState<RegisterFormValues>(EMPTY_REGISTER_FORM);
    const [touched, setTouched] = useState(false);

    const errors = validateRegisterForm(values);
    const showError = (field: keyof RegisterFormValues) => (touched ? errors[field] : undefined);

    function update(patch: Partial<RegisterFormValues>) {
        setValues({ ...values, ...patch });
    }

    function submit() {
        setTouched(true);
        if (!isRegisterFormValid(values) || register.isPending) {
            return;
        }
        register.mutate(toRegisterPayload(values), {
            onSuccess: () => navigate("/", { replace: true }),
        });
    }

    return (
        <div className="brand-entry flex h-full min-h-0 flex-col items-center overflow-y-auto overscroll-y-contain bg-app px-4 py-6 sm:py-10">
            <div className="my-auto flex w-full max-w-md shrink-0 flex-col gap-6">
                <header className="flex flex-col gap-1 text-center">
                    <BrandLogo variant="tagline" className="mx-auto mb-4 w-[340px]" />
                    <h1 className="text-xl font-semibold tracking-[-0.025em] text-primary">
                        Регистрация
                    </h1>
                    <p className="text-[13px] text-muted">
                        Нужен код приглашения: регистрация закрыта для посторонних
                    </p>
                </header>

                <Card className="p-5 shadow-elevated">
                    <form
                        className="flex flex-col gap-4"
                        onSubmit={(event) => {
                            event.preventDefault();
                            submit();
                        }}
                    >
                        {register.error && (
                            <ErrorMessage
                                title="Не удалось зарегистрироваться"
                                message={(register.error as Error).message}
                            />
                        )}

                        <div className="grid gap-4 sm:grid-cols-2">
                            <Field label="Фамилия" error={showError("lastName")}>
                                {(id) => (
                                    <Input
                                        id={id}
                                        autoFocus={!touch}
                                        value={values.lastName}
                                        onChange={(event) => update({ lastName: event.target.value })}
                                    />
                                )}
                            </Field>

                            <Field label="Имя" error={showError("firstName")}>
                                {(id) => (
                                    <Input
                                        id={id}
                                        value={values.firstName}
                                        onChange={(event) =>
                                            update({ firstName: event.target.value })
                                        }
                                    />
                                )}
                            </Field>
                        </div>

                        <Field label="Логин" error={showError("username")}>
                            {(id) => (
                                <Input
                                    id={id}
                                    autoComplete="username"
                                    placeholder="boris"
                                    value={values.username}
                                    onChange={(event) => update({ username: event.target.value })}
                                />
                            )}
                        </Field>

                        <div className="grid gap-4 sm:grid-cols-2">
                            <Field
                                label="Пароль"
                                hint="Не короче 8 символов"
                                error={showError("password")}
                            >
                                {(id) => (
                                    <Input
                                        id={id}
                                        type="password"
                                        autoComplete="new-password"
                                        value={values.password}
                                        onChange={(event) => update({ password: event.target.value })}
                                    />
                                )}
                            </Field>

                            <Field label="Повторите пароль" error={showError("passwordConfirm")}>
                                {(id) => (
                                    <Input
                                        id={id}
                                        type="password"
                                        autoComplete="new-password"
                                        value={values.passwordConfirm}
                                        onChange={(event) =>
                                            update({ passwordConfirm: event.target.value })
                                        }
                                    />
                                )}
                            </Field>
                        </div>

                        <Field
                            label="Код приглашения"
                            error={showError("inviteCode")}
                        >
                            {(id) => (
                                <Input
                                    id={id}
                                    value={values.inviteCode}
                                    onChange={(event) => update({ inviteCode: event.target.value })}
                                />
                            )}
                        </Field>

                        <Button
                            type="submit"
                            variant="primary"
                            size="lg"
                            icon={<UserPlus size={15} />}
                            disabled={register.isPending}
                        >
                            Зарегистрироваться
                        </Button>

                        <p className="text-center text-[12px] text-muted">
                            Отчество, почту, телефон и фотографию можно добавить позже в профиле.
                        </p>
                    </form>
                </Card>

                <p className="text-center text-[13px] text-muted">
                    Уже есть учётная запись?{" "}
                    <Link to="/login" className="text-accent hover:text-accent-hover">
                        Войти
                    </Link>
                </p>
            </div>
        </div>
    );
}
