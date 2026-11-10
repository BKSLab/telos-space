import { Eye, Users } from "lucide-react";
import type { ProjectMember } from "@/lib/types";
import { fullName } from "@/lib/types";
import { Field, Select } from "@/components/ui/Field";
import { Popover } from "@/components/ui/Popover";

interface TaskPeopleFieldsProps {
    members: ProjectMember[];
    executorId: number | null;
    reporterId: number | null;
    observerIds: number[];
    onExecutorChange: (userId: number | null) => void;
    onReporterChange: (userId: number | null) => void;
    onObserversChange: (userIds: number[]) => void;
    disabled?: boolean;
    assigneeSnapshot?: string | null;
    canChangeReporter?: boolean;
}

/**
 * Единый выбор людей для формы создания и Task Drawer. Источником всегда
 * служит команда проекта — глобального каталога пользователей здесь нет.
 */
export function TaskPeopleFields({
    members,
    executorId,
    reporterId,
    observerIds,
    onExecutorChange,
    onReporterChange,
    onObserversChange,
    disabled = false,
    assigneeSnapshot,
    canChangeReporter = true,
}: TaskPeopleFieldsProps) {
    const selectedObservers = new Set(observerIds);

    return (
        <div className="grid gap-3 sm:grid-cols-2">
            <Field
                label="Исполнитель"
                hint={
                    executorId === null && assigneeSnapshot
                        ? `Ранее указано: ${assigneeSnapshot}`
                        : undefined
                }
            >
                {(id) => (
                    <Select
                        id={id}
                        value={executorId ?? ""}
                        disabled={disabled}
                        onChange={(event) =>
                            onExecutorChange(
                                event.target.value ? Number(event.target.value) : null,
                            )
                        }
                    >
                        <option value="">Не назначен</option>
                        {members.map((member) => (
                            <option key={member.user.id} value={member.user.id}>
                                {memberOptionLabel(member)}
                            </option>
                        ))}
                    </Select>
                )}
            </Field>

            <Field
                label="Постановщик"
                hint={!canChangeReporter ? "Изменяет только владелец" : undefined}
            >
                {(id) => (
                    <Select
                        id={id}
                        value={reporterId ?? ""}
                        disabled={disabled || !canChangeReporter}
                        onChange={(event) =>
                            onReporterChange(
                                event.target.value ? Number(event.target.value) : null,
                            )
                        }
                    >
                        <option value="">Не указан</option>
                        {members.map((member) => (
                            <option key={member.user.id} value={member.user.id}>
                                {memberOptionLabel(member)}
                            </option>
                        ))}
                    </Select>
                )}
            </Field>

            <div className="sm:col-span-2">
                <span className="mb-1.5 block text-xs font-medium text-secondary">
                    Наблюдатели
                </span>
                <Popover
                    label="Выбор наблюдателей"
                    align="start"
                    disabled={disabled}
                    triggerClassName="w-full justify-between font-normal"
                    trigger={
                        <>
                            <span className="inline-flex min-w-0 items-center gap-2">
                                <Eye size={14} aria-hidden="true" />
                                <span className="truncate">
                                    {observerIds.length > 0
                                        ? `Выбрано: ${observerIds.length}`
                                        : "Не выбраны"}
                                </span>
                            </span>
                            <Users size={14} aria-hidden="true" />
                        </>
                    }
                >
                    <div className="flex max-h-64 flex-col overflow-y-auto">
                        <p className="px-2 pb-2 text-[10px] font-semibold tracking-[0.12em] text-muted uppercase">
                            Наблюдатели задачи
                        </p>
                        {members.map((member) => {
                            const checked = selectedObservers.has(member.user.id);
                            return (
                                <label
                                    key={member.user.id}
                                    className="flex cursor-pointer items-center gap-2.5 rounded-control px-2 py-2 text-[13px] text-secondary hover:bg-white/[0.035]"
                                >
                                    <input
                                        type="checkbox"
                                        checked={checked}
                                        disabled={disabled}
                                        className="size-3.5 accent-accent"
                                        onChange={() =>
                                            onObserversChange(
                                                checked
                                                    ? observerIds.filter(
                                                          (userId) =>
                                                              userId !== member.user.id,
                                                      )
                                                    : [...observerIds, member.user.id],
                                            )
                                        }
                                    />
                                    <span className="min-w-0">
                                        <span className="block truncate text-primary">
                                            {fullName(member.user)}
                                        </span>
                                        <span className="block truncate font-mono text-[10px] text-muted">
                                            @{member.user.username}
                                            {member.role === "OWNER" ? " · владелец" : ""}
                                        </span>
                                    </span>
                                </label>
                            );
                        })}
                    </div>
                </Popover>
            </div>
        </div>
    );
}

function memberOptionLabel(member: ProjectMember): string {
    const owner = member.role === "OWNER" ? " · владелец" : "";
    return `${fullName(member.user)} · @${member.user.username}${owner}`;
}
