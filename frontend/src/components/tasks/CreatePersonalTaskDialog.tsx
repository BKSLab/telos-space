import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { PersonalTaskCreate, Project, ProjectMember, Task, TaskCreate, TaskKind, TaskPriority, UserSummary } from "@/lib/types";
import { PRIORITY_LABELS, PRIORITY_ORDER } from "@/lib/types";
import { useToast } from "@/lib/toast";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Field, Input, Select, Textarea } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { LocalUserSelect } from "./LocalUserSelect";
import { TaskPeopleFields } from "./TaskPeopleFields";

export function CreatePersonalTaskDialog({ onClose, onCreated, initialDueDate, submitTask }: {
    onClose: () => void; onCreated?: (task: Task) => void; initialDueDate?: string;
    submitTask?: (kind: TaskKind, destinationId: number | null, data: TaskCreate | PersonalTaskCreate) => Promise<Task>;
}) {
    const client = useQueryClient();
    const toast = useToast();
    const [kind, setKind] = useState<TaskKind>("PERSONAL");
    const [projectId, setProjectId] = useState<number | null>(null);
    const [title, setTitle] = useState("");
    const [description, setDescription] = useState("");
    const [priority, setPriority] = useState<TaskPriority>("MEDIUM");
    const [startDate, setStartDate] = useState("");
    const [dueDate, setDueDate] = useState(initialDueDate ?? "");
    const [executor, setExecutor] = useState<UserSummary[]>([]);
    const [observers, setObservers] = useState<UserSummary[]>([]);
    const [memberExecutorId, setMemberExecutorId] = useState<number | null>(null);
    const [memberObserverIds, setMemberObserverIds] = useState<number[]>([]);
    const projects = useQuery({ queryKey: queryKeys.projects, staleTime: 30_000, queryFn: () => api.get<Project[]>(endpoints.projects()), enabled: kind === "PROJECT" });
    const members = useQuery({ queryKey: queryKeys.projectMembers(projectId ?? 0), queryFn: () => api.get<ProjectMember[]>(endpoints.projectMembers(projectId!)), enabled: kind === "PROJECT" && projectId !== null });
    const invalidDates = Boolean(startDate && dueDate && startDate > dueDate);
    const create = useMutation({
        mutationFn: () => {
            const body = { title: title.trim(), description_md: description.trim() || null, priority, start_date: startDate || null, due_date: dueDate || null,
                executor_id: kind === "PROJECT" ? memberExecutorId : executor[0]?.id ?? null,
                observer_ids: kind === "PROJECT" ? memberObserverIds : observers.map(user => user.id) };
            return submitTask ? submitTask(kind, projectId, body) : api.post<Task>(kind === "PROJECT" ? endpoints.projectTasks(projectId!) : endpoints.personalTasks(), body);
        },
        onSuccess: task => {
            void client.invalidateQueries({ queryKey: queryKeys.myTasks });
            void client.invalidateQueries({ queryKey: queryKeys.dashboard });
            if (projectId) void client.invalidateQueries({ queryKey: ["projects", projectId] });
            toast.success(`Задача ${task.key} создана`); onCreated?.(task); onClose();
        },
    });
    return <Modal isOpen title="Новая задача" description="Разовая задача или задача проекта" size="md" tall isDismissable={!create.isPending} onOpenChange={open => !open && onClose()}
        footer={<><Button disabled={create.isPending} onClick={onClose}>Отмена</Button><Button variant="primary" disabled={!title.trim() || invalidDates || (kind === "PROJECT" && projectId === null) || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "Создаём…" : "Создать задачу"}</Button></>}>
        <div className="flex flex-col gap-4">
            {create.error && <ErrorMessage message={(create.error as Error).message} />}
            <div className="flex gap-2" role="group" aria-label="Куда поставить задачу">
                <Button variant={kind === "PERSONAL" ? "primary" : "ghost"} onClick={() => setKind("PERSONAL")}>Разовая</Button>
                <Button variant={kind === "PROJECT" ? "primary" : "ghost"} onClick={() => setKind("PROJECT")}>В проект</Button>
            </div>
            {kind === "PROJECT" && <Field label="Проект">{id => <Select id={id} value={projectId ?? ""} onChange={event => { setProjectId(Number(event.target.value) || null); setMemberExecutorId(null); setMemberObserverIds([]); }}><option value="">Выберите проект…</option>{projects.data?.map(project => <option key={project.id} value={project.id}>{project.key} · {project.name}</option>)}</Select>}</Field>}
            <Field label="Задача">{id => <Input id={id} autoFocus maxLength={512} value={title} onChange={event => setTitle(event.target.value)} />}</Field>
            <Field label="Описание">{id => <Textarea id={id} rows={4} value={description} onChange={event => setDescription(event.target.value)} />}</Field>
            <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Приоритет">{id => <Select id={id} value={priority} onChange={event => setPriority(event.target.value as TaskPriority)}>{PRIORITY_ORDER.map(value => <option key={value} value={value}>{PRIORITY_LABELS[value]}</option>)}</Select>}</Field>
                <Field label="Начало">{id => <Input id={id} type="date" value={startDate} max={dueDate || undefined} onChange={event => setStartDate(event.target.value)} />}</Field>
                <Field label="Срок">{id => <Input id={id} type="date" value={dueDate} min={startDate || undefined} onChange={event => setDueDate(event.target.value)} />}</Field>
            </div>
            {invalidDates && <p role="alert" className="text-sm text-danger">Начало не может быть позже срока.</p>}
            {kind === "PERSONAL" ? <><Field label="Исполнитель">{() => <LocalUserSelect label="Исполнитель" selected={executor} onChange={setExecutor} />}</Field><Field label="Наблюдатели">{() => <LocalUserSelect label="Наблюдатели" selected={observers} onChange={setObservers} multiple />}</Field></> : projectId && <TaskPeopleFields members={members.data ?? []} executorId={memberExecutorId} reporterId={null} observerIds={memberObserverIds} onExecutorChange={setMemberExecutorId} onReporterChange={() => {}} onObserversChange={setMemberObserverIds} canChangeReporter={false} />}
            <p className="text-xs text-muted">Постановщиком станете вы. Задача появится в «Моих задачах» у её участников.</p>
        </div>
    </Modal>;
}
