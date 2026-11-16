import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GripVertical, Plus, Trash2 } from "lucide-react";
import { api, ApiError, endpoints, queryKeys } from "@/lib/api";
import type { Project, ProjectStage } from "@/lib/types";
import { PROJECT_COLORS } from "@/lib/types";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { useCurrentUser } from "@/lib/useAuth";
import { Button, IconButton } from "@/components/ui/Button";
import { Card, Section } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { StatusDot } from "@/components/ui/Badge";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { useToast } from "@/lib/toast";
import { ProjectForm } from "@/components/projects/ProjectForm";
import { ProjectPeopleSummary } from "@/components/projects/ProjectPeopleSummary";
import { ProjectDeadlineHistory } from "@/components/projects/ProjectDeadlineHistory";
import { ProjectContextHistory } from "@/components/projects/ProjectContextHistory";
import { ProjectTeamPage } from "@/routes/ProjectTeamPage";
import {
    isProjectFormValid,
    requiresDeadlineComment,
    rebaseProjectFormValues,
    toProjectFormValues,
    toProjectUpdatePayload,
    type ProjectFormValues,
} from "@/lib/projectForm";

export function ProjectSettingsPage() {
    const project = useProjectOutlet();
    return <ProjectSettingsEditor key={project.id} project={project} />;
}

function ProjectSettingsEditor({ project }: { project: Project }) {
    const currentUser = useCurrentUser();
    const canEditProject = currentUser.data != null && project.owner_id != null && currentUser.data.id === project.owner_id;
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const toast = useToast();
    const [values, setValues] = useState<ProjectFormValues>(() => toProjectFormValues(project));
    const [formProject, setFormProject] = useState(project);
    const [conflictingProject, setConflictingProject] = useState<Project | null>(null);
    const [newStageName, setNewStageName] = useState("");
    const [isDeleteOpen, setDeleteOpen] = useState(false);
    const [deleteConfirmation, setDeleteConfirmation] = useState("");
    const commentRequired = requiresDeadlineComment(formProject, values);
    const active = useRef(true);

    useEffect(() => {
        active.current = true;
        return () => { active.current = false; };
    }, []);

    const stagesQuery = useQuery({
        queryKey: queryKeys.stages(project.id),
        queryFn: () => api.get<ProjectStage[]>(endpoints.projectStages(project.id)),
    });

    const invalidateProject = () => {
        queryClient.invalidateQueries({ queryKey: queryKeys.projects });
        queryClient.invalidateQueries({ queryKey: ["projects", project.id] });
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
        queryClient.invalidateQueries({ queryKey: queryKeys.projectContextHistory(project.id) });
    };

    const saveMutation = useMutation({
        mutationFn: () =>
            api.patch<Project>(endpoints.project(project.id), toProjectUpdatePayload(values, formProject)),
        onSuccess: (saved) => {
            invalidateProject();
            if (!active.current) return;
            setValues(toProjectFormValues(saved));
            setFormProject(saved);
            setConflictingProject(null);
            toast.success("Настройки проекта сохранены");
        },
        onError: async (error) => {
            if (error instanceof ApiError && error.status === 409) {
                invalidateProject();
                if (!active.current) return;
                try {
                    const latest = await api.get<Project>(endpoints.project(project.id));
                    if (active.current) setConflictingProject(latest);
                } catch {
                    if (active.current) toast.error("Не удалось загрузить актуальные данные. Обновите страницу перед повторным сохранением.");
                }
            }
        },
    });

    const createStageMutation = useMutation({
        mutationFn: () =>
            api.post<ProjectStage>(endpoints.projectStages(project.id), {
                name: newStageName.trim(),
                color: PROJECT_COLORS[(stagesQuery.data?.length ?? 0) % PROJECT_COLORS.length],
            }),
        onSuccess: () => {
            setNewStageName("");
            queryClient.invalidateQueries({ queryKey: queryKeys.stages(project.id) });
            invalidateProject();
        },
        onError: (error) => toast.error((error as Error).message),
    });

    const updateStageMutation = useMutation({
        mutationFn: ({ stageId, data }: { stageId: number; data: Record<string, unknown> }) =>
            api.patch<ProjectStage>(endpoints.stage(stageId), data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: queryKeys.stages(project.id) });
            queryClient.invalidateQueries({ queryKey: ["projects", project.id] });
            invalidateProject();
        },
        onError: (error) => toast.error((error as Error).message),
    });

    const deleteStageMutation = useMutation({
        mutationFn: (stageId: number) => api.delete(endpoints.stage(stageId)),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: queryKeys.stages(project.id) });
            invalidateProject();
        },
        onError: (error) => toast.error((error as Error).message),
    });

    const deleteProjectMutation = useMutation({
        mutationFn: () => api.delete(endpoints.project(project.id)),
        onSuccess: () => {
            invalidateProject();
            navigate("/?view=projects");
        },
    });

    return (
        <div className="scrollbar-thin h-full overflow-y-auto">
            <div className="mx-auto flex w-full min-w-0 max-w-none flex-col gap-6 px-3 py-4 pb-24 sm:px-5 sm:py-5">
                <Section title="О проекте">
                    <div className="flex min-w-0 flex-col gap-5 rounded-[var(--radius-card)] bg-surface/40 p-3 sm:p-5">
                        <ProjectPeopleSummary project={project} />
                        {saveMutation.error && (
                            <ErrorMessage message={(saveMutation.error as Error).message} />
                        )}
                        {!canEditProject && <p className="text-[13px] text-muted">Паспорт и настройки редактирует владелец проекта.</p>}
                        {conflictingProject && <div className="flex flex-col gap-3 rounded-[var(--radius-control)] border border-line bg-surface-2 p-3 text-[13px]">
                            <p className="text-primary">Проект изменился во время редактирования. Ваш черновик сохранён. Сравните его с актуальным паспортом.</p>
                            <details>
                                <summary className="cursor-pointer text-accent">Актуальный паспорт</summary>
                                <div className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap break-words text-secondary">{conflictingProject.description_md || "Паспорт пока не заполнен."}</div>
                            </details>
                            <div className="flex flex-wrap gap-2">
                                <Button size="sm" onClick={() => {
                                    setValues(toProjectFormValues(conflictingProject));
                                    setFormProject(conflictingProject);
                                    setConflictingProject(null);
                                    saveMutation.reset();
                                }}>Принять актуальную версию</Button>
                                <Button size="sm" onClick={() => {
                                    setValues(rebaseProjectFormValues(values, formProject, conflictingProject));
                                    setFormProject(conflictingProject);
                                    setConflictingProject(null);
                                    saveMutation.reset();
                                }}>Продолжить с моими правками</Button>
                            </div>
                        </div>}
                        <ProjectForm project={project} onProjectChange={saved => { queryClient.setQueryData(queryKeys.project(project.id), saved); void queryClient.invalidateQueries({ queryKey: queryKeys.projects }); void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard }); }} values={values} onChange={setValues} lockKey requireDeadlineComment={commentRequired} disabled={!canEditProject || saveMutation.isPending} />
                        {commentRequired && <p className="text-[12px] text-muted">Предыдущий срок: {formProject.due_date ?? "без даты"}.</p>}
                        <div className="flex justify-end pt-1">
                            <Button
                                variant="primary"
                                disabled={!canEditProject || !isProjectFormValid(values) || (commentRequired && !values.due_date_comment.trim()) || saveMutation.isPending || conflictingProject !== null || Object.keys(toProjectUpdatePayload(values, formProject)).length === 0}
                                onClick={() => saveMutation.mutate()}
                            >
                                Сохранить
                            </Button>
                        </div>
                    </div>
                </Section>

                <ProjectDeadlineHistory projectId={project.id} />
                <ProjectContextHistory projectId={project.id} />
                <ProjectTeamPage embedded />

                <Section title="Стадии канбана">
                    <Card className="flex flex-col gap-3 p-4">
                        {stagesQuery.isPending && <Skeleton className="h-24 w-full" />}
                        {stagesQuery.error && <ErrorMessage message={(stagesQuery.error as Error).message} action={<Button onClick={() => stagesQuery.refetch()}>Повторить</Button>} />}
                        {stagesQuery.data?.map((stage, index) => (
                            <div
                                key={stage.id}
                                className="flex flex-wrap items-center gap-2 rounded-[var(--radius-control)] bg-white/[0.025] px-2.5 py-2"
                            >
                                <GripVertical
                                    size={14}
                                    aria-hidden="true"
                                    className="shrink-0 text-disabled"
                                />
                                <StatusDot color={stage.color} />
                                <Input
                                    aria-label={`Название стадии ${stage.name}`}
                                    defaultValue={stage.name}
                                    className="w-40"
                                    onBlur={(event) => {
                                        const name = event.target.value.trim();
                                        if (name !== "" && name !== stage.name) {
                                            updateStageMutation.mutate({
                                                stageId: stage.id,
                                                data: { name },
                                            });
                                        }
                                    }}
                                />
                                <input
                                    type="color"
                                    aria-label={`Цвет стадии ${stage.name}`}
                                    defaultValue={stage.color}
                                    className="h-8 w-10 cursor-pointer rounded-md border border-line bg-surface-2"
                                    onBlur={(event) => {
                                        if (event.target.value !== stage.color) {
                                            updateStageMutation.mutate({
                                                stageId: stage.id,
                                                data: { color: event.target.value },
                                            });
                                        }
                                    }}
                                />
                                <label className="inline-flex items-center gap-1.5 text-[12px] text-muted">
                                    <input
                                        type="checkbox"
                                        checked={stage.is_done_stage}
                                        className="accent-[var(--color-accent)]"
                                        onChange={(event) =>
                                            updateStageMutation.mutate({
                                                stageId: stage.id,
                                                data: { is_done_stage: event.target.checked },
                                            })
                                        }
                                    />
                                    Завершающая
                                </label>
                                <div className="ml-auto flex items-center gap-1">
                                    <IconButton
                                        label={`Поднять стадию ${stage.name}`}
                                        size="sm"
                                        disabled={index === 0 || updateStageMutation.isPending}
                                        onClick={() =>
                                            updateStageMutation.mutate({
                                                stageId: stage.id,
                                                data: { order_index: Math.max(index - 1, 0) },
                                            })
                                        }
                                    >
                                        ↑
                                    </IconButton>
                                    <IconButton
                                        label={`Опустить стадию ${stage.name}`}
                                        size="sm"
                                        disabled={index === (stagesQuery.data?.length ?? 1) - 1 || updateStageMutation.isPending}
                                        onClick={() =>
                                            updateStageMutation.mutate({
                                                stageId: stage.id,
                                                data: { order_index: index + 1 },
                                            })
                                        }
                                    >
                                        ↓
                                    </IconButton>
                                    <IconButton
                                        label={`Удалить стадию ${stage.name}`}
                                        size="sm"
                                        variant="destructive"
                                        disabled={deleteStageMutation.isPending}
                                        onClick={() => deleteStageMutation.mutate(stage.id)}
                                    >
                                        <Trash2 size={12} aria-hidden="true" />
                                    </IconButton>
                                </div>
                            </div>
                        ))}

                        <div className="flex gap-2 border-t border-line-subtle pt-3">
                            <Input
                                value={newStageName}
                                aria-label="Название новой стадии"
                                placeholder="Название новой стадии"
                                onChange={(event) => setNewStageName(event.target.value)}
                            />
                            <Button
                                icon={<Plus size={14} />}
                                disabled={
                                    newStageName.trim() === "" || createStageMutation.isPending
                                }
                                onClick={() => createStageMutation.mutate()}
                            >
                                Добавить
                            </Button>
                        </div>
                    </Card>
                </Section>

                <Section title="Опасная зона">
                    <Card className="flex flex-wrap items-center justify-between gap-3 border-danger/25 p-4">
                        <div className="flex min-w-0 flex-col gap-0.5">
                            <p className="text-[13px] font-medium text-secondary">Удалить проект</p>
                            <p className="text-[12px] text-muted">
                                Вместе с проектом удаляются его задачи, структура, документы.
                            </p>
                        </div>
                        <Button variant="destructive" onClick={() => setDeleteOpen(true)}>
                            Удалить проект
                        </Button>
                    </Card>
                </Section>
            </div>

            <Modal
                title={`Удалить проект «${project.name}»?`}
                description="Действие необратимо. Задачи, структура ИСР, документы будут удалены."
                isOpen={isDeleteOpen}
                onOpenChange={(open) => {
                    if (!open) {
                        setDeleteOpen(false);
                        setDeleteConfirmation("");
                    }
                }}
                footer={
                    <>
                        <Button onClick={() => setDeleteOpen(false)}>Отмена</Button>
                        <Button
                            variant="destructive"
                            disabled={
                                deleteConfirmation.toUpperCase() !== project.key ||
                                deleteProjectMutation.isPending
                            }
                            onClick={() => deleteProjectMutation.mutate()}
                        >
                            Удалить навсегда
                        </Button>
                    </>
                }
            >
                <div className="flex flex-col gap-3">
                    {deleteProjectMutation.error && (
                        <ErrorMessage message={(deleteProjectMutation.error as Error).message} />
                    )}
                    <Field
                        label={`Введите код проекта «${project.key}» для подтверждения`}
                    >
                        {(id) => (
                            <Input
                                id={id}
                                value={deleteConfirmation}
                                className="font-mono uppercase"
                                onChange={(event) =>
                                    setDeleteConfirmation(event.target.value.toUpperCase())
                                }
                            />
                        )}
                    </Field>
                </div>
            </Modal>
        </div>
    );
}
