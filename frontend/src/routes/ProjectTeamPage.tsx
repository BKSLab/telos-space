import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Crown, Trash2, UserRoundPlus, Users } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { ProjectMember } from "@/lib/types";
import { fullName, initials } from "@/lib/types";
import { useCurrentUser } from "@/lib/useAuth";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { Badge } from "@/components/ui/Badge";
import { Button, IconButton } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";

export function ProjectTeamPage({ embedded = false }: { embedded?: boolean }) {
    const project = useProjectOutlet();
    const queryClient = useQueryClient();
    const currentUserQuery = useCurrentUser();
    const [username, setUsername] = useState("");
    const [memberToRemove, setMemberToRemove] = useState<ProjectMember | null>(null);

    const membersQuery = useQuery({
        queryKey: queryKeys.projectMembers(project.id),
        queryFn: () => api.get<ProjectMember[]>(endpoints.projectMembers(project.id)),
    });
    const members = membersQuery.data ?? [];
    const isOwner = members.some(
        (member) =>
            member.role === "OWNER" && member.user.id === currentUserQuery.data?.id,
    );

    const refreshMembers = () => {
        queryClient.invalidateQueries({ queryKey: queryKeys.projectMembers(project.id) });
        queryClient.invalidateQueries({ queryKey: queryKeys.projects });
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
    };

    const addMutation = useMutation({
        mutationFn: () =>
            api.post<ProjectMember>(endpoints.projectMembers(project.id), {
                username: username.trim(),
            }),
        onSuccess: () => {
            setUsername("");
            refreshMembers();
        },
    });

    const removeMutation = useMutation({
        mutationFn: (member: ProjectMember) =>
            api.delete<void>(endpoints.projectMember(project.id, member.user.id)),
        onSuccess: () => {
            setMemberToRemove(null);
            refreshMembers();
            queryClient.invalidateQueries({ queryKey: ["projects", project.id, "tasks"] });
        },
    });

    function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (username.trim()) addMutation.mutate();
    }

    return (
        <div className={embedded ? "flex w-full flex-col gap-4" : "mx-auto flex w-full max-w-none flex-col gap-7 px-5 py-6"}>
            <header className="flex flex-wrap items-start justify-between gap-4">
                <div>
                    <div className="flex items-center gap-2">
                        <Users size={17} aria-hidden="true" className="text-muted" />
                        <h2 className="text-lg font-semibold tracking-[-0.02em] text-primary">
                            Команда
                        </h2>
                    </div>
                    <p className="mt-1 text-[13px] text-muted">
                        {members.length} участников · доступ к проекту и назначениям задач
                    </p>
                </div>
            </header>

            {isOwner && (
                <section aria-labelledby="add-team-member-title" className="max-w-2xl">
                    <h3
                        id="add-team-member-title"
                        className="text-[11px] font-semibold tracking-[0.1em] text-muted uppercase"
                    >
                        Добавить участника
                    </h3>
                    <form
                        onSubmit={submit}
                        className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]"
                    >
                        <div className="min-w-0">
                            <Field
                                label="Точный логин"
                                hint="Поиск и подсказки отключены — логин должен быть известен заранее."
                            >
                                {(id) => (
                                    <Input
                                        id={id}
                                        value={username}
                                        maxLength={50}
                                        autoComplete="off"
                                        autoCapitalize="none"
                                        spellCheck={false}
                                        placeholder="username"
                                        onChange={(event) => setUsername(event.target.value)}
                                    />
                                )}
                            </Field>
                        </div>
                        <Button
                            type="submit"
                            variant="primary"
                            className="w-full sm:mt-[22px] sm:w-auto sm:self-start"
                            disabled={!username.trim() || addMutation.isPending}
                        >
                            <UserRoundPlus size={14} aria-hidden="true" />
                            Добавить
                        </Button>
                    </form>
                    {addMutation.error && (
                        <div className="mt-3">
                            <ErrorMessage
                                title="Не удалось добавить участника"
                                message={(addMutation.error as Error).message}
                            />
                        </div>
                    )}
                </section>
            )}

            {!isOwner && !membersQuery.isPending && (
                <p className="text-[12px] text-muted">
                    Составом команды управляет владелец проекта.
                </p>
            )}

            <section aria-labelledby="project-members-title">
                <div className="flex items-center justify-between border-b border-line-subtle pb-2">
                    <h3
                        id="project-members-title"
                        className="text-[11px] font-semibold tracking-[0.1em] text-muted uppercase"
                    >
                        Участники
                    </h3>
                    <span className="font-mono text-[11px] text-disabled">{members.length}</span>
                </div>

                {membersQuery.isPending && (
                    <div role="status" aria-label="Загрузка команды" className="mt-3 space-y-2">
                        <Skeleton className="h-14 w-full" />
                        <Skeleton className="h-14 w-full" />
                    </div>
                )}
                {membersQuery.error && (
                    <div className="mt-4">
                        <ErrorMessage message={(membersQuery.error as Error).message} />
                    </div>
                )}
                {!membersQuery.isPending && !membersQuery.error && members.length === 0 && (
                    <EmptyState
                        title="Команда пока пуста"
                        description="Владелец проекта появится здесь автоматически."
                    />
                )}

                <ul className="divide-y divide-line-subtle">
                    {members.map((member) => {
                        const owner = member.role === "OWNER";
                        const current = member.user.id === currentUserQuery.data?.id;
                        return (
                            <li
                                key={member.id}
                                className="flex min-w-0 items-center gap-3 py-3.5"
                            >
                                <span
                                    aria-hidden="true"
                                    className="flex size-9 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[11px] font-semibold text-secondary"
                                >
                                    {initials(member.user)}
                                </span>
                                <div className="min-w-0 flex-1">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="truncate text-[13px] font-medium text-primary">
                                            {fullName(member.user)}
                                        </span>
                                        {owner && (
                                            <Badge
                                                title="Владелец проекта"
                                                className="border-accent-border bg-accent-soft text-accent"
                                            >
                                                <Crown size={11} aria-hidden="true" />
                                                Владелец
                                            </Badge>
                                        )}
                                        {current && !owner && <Badge>Вы</Badge>}
                                    </div>
                                    <p className="mt-0.5 truncate font-mono text-[11px] text-muted">
                                        @{member.user.username}
                                    </p>
                                </div>
                                {isOwner && !owner && (
                                    <IconButton
                                        label={`Удалить ${fullName(member.user)} из команды`}
                                        variant="destructive"
                                        onClick={() => setMemberToRemove(member)}
                                    >
                                        <Trash2 size={14} aria-hidden="true" />
                                    </IconButton>
                                )}
                            </li>
                        );
                    })}
                </ul>
            </section>

            <Modal
                title="Удалить участника?"
                description={
                    memberToRemove
                        ? `${fullName(memberToRemove.user)} потеряет доступ к проекту. Назначения в задачах будут сняты.`
                        : undefined
                }
                isOpen={memberToRemove !== null}
                onOpenChange={(open) => {
                    if (!open) setMemberToRemove(null);
                }}
                footer={
                    <>
                        <Button onClick={() => setMemberToRemove(null)}>Отмена</Button>
                        <Button
                            variant="destructive"
                            disabled={removeMutation.isPending}
                            onClick={() => {
                                if (memberToRemove) removeMutation.mutate(memberToRemove);
                            }}
                        >
                            Удалить из команды
                        </Button>
                    </>
                }
            >
                {removeMutation.error && (
                    <ErrorMessage message={(removeMutation.error as Error).message} />
                )}
            </Modal>
        </div>
    );
}
