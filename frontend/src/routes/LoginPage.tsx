import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { LogIn } from "lucide-react";
import { useLogin } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { PRODUCT_NAME } from "@/lib/brand";
import { useMediaQuery } from "@/lib/useMediaQuery";

interface LocationState {
    from?: string;
}

export function LoginPage() {
    const navigate = useNavigate();
    const location = useLocation();
    const login = useLogin();
    const touch = useMediaQuery("(pointer: coarse)");
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");

    const from = (location.state as LocationState | null)?.from ?? "/";
    const canSubmit = username.trim() !== "" && password !== "" && !login.isPending;

    function submit() {
        if (!canSubmit) {
            return;
        }
        login.mutate(
            { username: username.trim(), password },
            { onSuccess: () => navigate(from, { replace: true }) },
        );
    }

    return (
        <div className="brand-entry flex h-full min-h-0 flex-col items-center overflow-y-auto overscroll-y-contain bg-app px-4 py-6 sm:py-10">
            <div className="my-auto flex w-full max-w-sm shrink-0 flex-col gap-6">
                <header className="flex flex-col items-center gap-4 text-center">
                    <h1 className="sr-only">Вход в {PRODUCT_NAME}</h1>
                    <BrandLogo variant="tagline" className="w-[340px]" />
                    <p className="max-w-xs text-[13px] leading-relaxed text-secondary">Цели, знания, задачи и результаты — в одном пространстве с помощником Арисом.</p>
                    <p className="text-[13px] text-muted">Войдите, чтобы продолжить работу</p>
                </header>

                <Card className="p-5 shadow-elevated">
                    <form
                        className="flex flex-col gap-4"
                        onSubmit={(event) => {
                            event.preventDefault();
                            submit();
                        }}
                    >
                        {login.error && (
                            <ErrorMessage
                                title="Не удалось войти"
                                message={(login.error as Error).message}
                            />
                        )}

                        <Field label="Логин">
                            {(id) => (
                                <Input
                                    id={id}
                                    autoFocus={!touch}
                                    autoComplete="username"
                                    value={username}
                                    onChange={(event) => setUsername(event.target.value)}
                                />
                            )}
                        </Field>

                        <Field label="Пароль">
                            {(id) => (
                                <Input
                                    id={id}
                                    type="password"
                                    autoComplete="current-password"
                                    value={password}
                                    onChange={(event) => setPassword(event.target.value)}
                                />
                            )}
                        </Field>

                        <Button
                            type="submit"
                            variant="primary"
                            size="lg"
                            icon={<LogIn size={15} />}
                            disabled={!canSubmit}
                        >
                            Войти
                        </Button>
                    </form>
                </Card>

                <p className="text-center text-[13px] text-muted">
                    Нет учётной записи?{" "}
                    <Link to="/register" className="text-accent hover:text-accent-hover">
                        Зарегистрироваться
                    </Link>
                </p>
            </div>
        </div>
    );
}
