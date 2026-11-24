import { useMemo, useState } from "react";
import { GitBranchPlus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import type { TaskListItem, TaskDependencyInput } from "@/lib/types";

interface DependencyDialogProps {
    tasks: TaskListItem[];
    isLoading: boolean;
    isSaving: boolean;
    onClose: () => void;
    onSave: (data: TaskDependencyInput) => void;
}

/** Создание единственного поддерживаемого типа связи Finish-to-Start. */
export function DependencyDialog({
    tasks,
    isLoading,
    isSaving,
    onClose,
    onSave,
}: DependencyDialogProps) {
    const sortedTasks = useMemo(
        () => [...tasks].sort((first, second) => first.number - second.number),
        [tasks],
    );
    const [predecessorId, setPredecessorId] = useState("");
    const [successorId, setSuccessorId] = useState("");
    const [lagDays, setLagDays] = useState("0");
    const isSameTask = predecessorId !== "" && predecessorId === successorId;
    const parsedLag = Number(lagDays);
    const isValid =
        predecessorId !== "" &&
        successorId !== "" &&
        !isSameTask &&
        Number.isInteger(parsedLag) &&
        parsedLag >= 0 &&
        parsedLag <= 3650;

    return (
        <Modal
            isOpen
            onOpenChange={(open) => !open && onClose()}
            title="Новая зависимость"
            description="Finish-to-Start: successor начинается после завершения predecessor"
            footer={
                <>
                    <Button variant="ghost" onClick={onClose} disabled={isSaving}>
                        Отмена
                    </Button>
                    <Button
                        variant="primary"
                        icon={<GitBranchPlus size={14} aria-hidden="true" />}
                        disabled={!isValid || isLoading || isSaving}
                        onClick={() =>
                            onSave({
                                predecessor_task_id: Number(predecessorId),
                                successor_task_id: Number(successorId),
                                dependency_type: "FINISH_TO_START",
                                lag_days: parsedLag,
                            })
                        }
                    >
                        {isSaving ? "Сохранение…" : "Связать"}
                    </Button>
                </>
            }
        >
            <div className="grid gap-3">
                <Field label="Predecessor">
                    {(id) => (
                        <Select
                            id={id}
                            value={predecessorId}
                            autoFocus
                            onChange={(event) => setPredecessorId(event.target.value)}
                        >
                            <option value="">Выберите задачу</option>
                            {sortedTasks.map((task) => (
                                <option key={task.id} value={task.id}>
                                    {task.key} · {task.title}
                                </option>
                            ))}
                        </Select>
                    )}
                </Field>
                <Field
                    label="Successor"
                    error={isSameTask ? "Задача не может зависеть сама от себя." : undefined}
                >
                    {(id) => (
                        <Select
                            id={id}
                            value={successorId}
                            onChange={(event) => setSuccessorId(event.target.value)}
                        >
                            <option value="">Выберите задачу</option>
                            {sortedTasks.map((task) => (
                                <option key={task.id} value={task.id}>
                                    {task.key} · {task.title}
                                </option>
                            ))}
                        </Select>
                    )}
                </Field>
                <Field label="Задержка, дней" hint="От 0 до 3650 календарных дней.">
                    {(id) => (
                        <Input
                            id={id}
                            type="number"
                            min={0}
                            max={3650}
                            value={lagDays}
                            onChange={(event) => setLagDays(event.target.value)}
                        />
                    )}
                </Field>
            </div>
        </Modal>
    );
}
