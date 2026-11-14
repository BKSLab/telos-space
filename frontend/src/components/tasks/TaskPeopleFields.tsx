import type { ProjectMember, UserSummary } from "@/lib/types";
import { Field } from "@/components/ui/Field";
import { UserSelect } from "@/components/users/UserSelect";

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

/** Project people fields only offer users from the supplied project team. */
export function TaskPeopleFields({
    members, executorId, reporterId, observerIds,
    onExecutorChange, onReporterChange, onObserversChange,
    disabled = false, assigneeSnapshot, canChangeReporter = true,
}: TaskPeopleFieldsProps) {
    const users = members.map(member => member.user);
    const byId = new Map(users.map(user => [user.id, user]));
    const selectedUser = (userId: number | null) => {
        const user = userId === null ? undefined : byId.get(userId);
        return user ? [user] : [];
    };
    const unknownSelection = (userId: number | null, name?: string | null) =>
        userId !== null && !byId.has(userId) ? { userId, name: name || `Участник #${userId}` } : undefined;
    const describeUser = (user: UserSummary) => `@${user.username}${members.find(member => member.user.id === user.id)?.role === "OWNER" ? " · владелец" : ""}`;
    const unknownObserverIds = observerIds.filter(userId => !byId.has(userId));
    const selectedObservers = observerIds.flatMap(userId => {
        const user = byId.get(userId);
        return user ? [user] : [];
    });

    return <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Исполнитель" hint={executorId === null && assigneeSnapshot ? `Ранее указано: ${assigneeSnapshot}` : undefined}>
            {id => <UserSelect id={id} label="Исполнитель" users={users} selected={selectedUser(executorId)}
                fallbackSelection={unknownSelection(executorId, assigneeSnapshot)} disabled={disabled}
                emptyLabel="Не назначен" describeUser={describeUser}
                onChange={selected => onExecutorChange(selected[0]?.id ?? null)} />}
        </Field>
        <Field label="Постановщик" hint={!canChangeReporter ? "Изменяет только владелец" : undefined}>
            {id => <UserSelect id={id} label="Постановщик" users={users} selected={selectedUser(reporterId)}
                fallbackSelection={unknownSelection(reporterId)} disabled={disabled || !canChangeReporter}
                emptyLabel="Не указан" describeUser={describeUser}
                onChange={selected => onReporterChange(selected[0]?.id ?? null)} />}
        </Field>
        <Field label="Наблюдатели" className="sm:col-span-2">
            {id => <UserSelect id={id} label="Выбор наблюдателей" users={users} selected={selectedObservers}
                fallbackSelection={unknownSelection(unknownObserverIds[0] ?? null)} multiple disabled={disabled}
                emptyLabel="Не выбраны" describeUser={describeUser}
                onChange={selected => onObserversChange([
                    ...observerIds.filter(userId => !byId.has(userId) || selected.some(user => user.id === userId)),
                    ...selected.filter(user => !observerIds.includes(user.id)).map(user => user.id),
                ])} onClear={() => onObserversChange([])} />}
        </Field>
    </div>;
}
