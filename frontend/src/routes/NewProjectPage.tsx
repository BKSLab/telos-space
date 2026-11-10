import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, endpoints, queryKeys } from "@/lib/api";
import { fullName, type Project, type ProjectStage } from "@/lib/types";
import { useCurrentUser } from "@/lib/useAuth";
import { useToast } from "@/lib/toast";
import { formatDateOnly } from "@/lib/dates";
import { Page } from "@/components/layout/AppShell";
import { Button, LinkButton } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/States";
import { ProjectForm } from "@/components/projects/ProjectForm";
import { Field, Input } from "@/components/ui/Field";
import { ProjectStagesDraft } from "@/components/projects/ProjectStagesDraft";
import { validStageDrafts, type StageDraft } from "@/lib/projectStages";
import {
    EMPTY_PROJECT_FORM,
    isProjectFormValid,
    toProjectPayload,
    type ProjectFormValues,
} from "@/lib/projectForm";

export function NewProjectPage() {
    const navigate = useNavigate();
    const toast = useToast();
    const [pendingImage, setPendingImage] = useState<File | null>(null);
    const queryClient = useQueryClient();
    const [values, setValues] = useState<ProjectFormValues>(() => ({ ...EMPTY_PROJECT_FORM, start_date: formatDateOnly(new Date()) }));
    const currentUser = useCurrentUser();
    const [memberNames, setMemberNames] = useState("");
    const [stageDrafts, setStageDrafts] = useState<StageDraft[] | null>(null);
    const defaults = useQuery({
        queryKey: queryKeys.projectDefaults,
        queryFn: () => api.get<{ stages: Pick<ProjectStage, "name" | "color" | "is_done_stage">[] }>(endpoints.projectDefaults()),
    });
    const stages = stageDrafts ?? (defaults.data?.stages ?? []).map((stage, index) => ({ ...stage, draftId: `default-${index}` }));
    const usernames = [...new Set(memberNames.split(/[\s,;]+/).map((name) => name.trim().toLowerCase()).filter(Boolean))];
    const validMembers = usernames.length <= 100 && usernames.every((name) => /^[a-z0-9_.-]{3,50}$/.test(name));

    const createMutation = useMutation({
        mutationFn: async () => { const project = await api.post<Project>(endpoints.projects(), {
            ...toProjectPayload(values), member_usernames: usernames,
            stages: stages.map(({ name, color, is_done_stage }) => ({ name, color, is_done_stage })),
        });
            if (pendingImage) {
                const body = new FormData(); body.append("file", pendingImage);
                try { return await api.postForm<Project>(endpoints.projectIconImage(project.id), body); }
                catch { toast.error("Проект создан, но картинка не загрузилась. Повторите загрузку в настройках проекта."); return project; }
            }
            return project;
        },
        onSuccess: (project) => {
            queryClient.invalidateQueries({ queryKey: queryKeys.projects });
            queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
            navigate(`/projects/${project.key}`);
        },
    });

    return (
        <Page className="max-w-3xl">
            <header className="flex flex-col gap-0.5">
                <h1 className="text-[22px] font-semibold tracking-[-0.03em] text-primary">
                    Новый проект
                </h1>
                <p className="text-[13px] text-muted">
                    Опишите проект, добавьте команду и настройте стадии канбана.
                </p>
            </header>

            <div className="flex flex-col gap-5 rounded-[var(--radius-panel)] bg-surface/45 p-5 sm:p-6">
                {createMutation.error && (
                    <ErrorMessage
                        title="Не удалось создать проект"
                        message={(createMutation.error as Error).message}
                    />
                )}

                <p className="text-[13px] text-secondary">
                    <span className="text-muted">Руководитель: </span>
                    {currentUser.data ? fullName(currentUser.data) : "Загрузка…"}
                </p>
                <ProjectForm pendingImage={pendingImage} onPendingImageChange={setPendingImage} values={values} onChange={setValues} disabled={createMutation.isPending} />
                <Field label="Команда" hint="Логины участников через запятую. Руководитель добавляется автоматически; состав можно изменить позже."
                    error={!validMembers ? "Проверьте логины участников: 3–50 символов, латиница, цифры, точка, дефис или подчёркивание." : undefined}>
                    {(id) => <Input id={id} value={memberNames} autoComplete="off" spellCheck={false}
                        placeholder="anna, ivan" onChange={(event) => setMemberNames(event.target.value)} />}
                </Field>
                <div className="border-t border-line-subtle pt-5">
                    {defaults.error ? <ErrorMessage message="Не удалось загрузить стадии канбана." action={<Button onClick={() => defaults.refetch()}>Повторить</Button>} />
                        : defaults.isPending ? <p role="status" className="text-[13px] text-muted">Загрузка стадий…</p>
                        : <ProjectStagesDraft stages={stages} onChange={setStageDrafts} />}
                </div>

                <div className="flex justify-end gap-2 pt-1">
                    <LinkButton to="/?view=projects">Отмена</LinkButton>
                    <Button
                        variant="primary"
                        disabled={!isProjectFormValid(values) || !values.start_date || !validMembers || !validStageDrafts(stages) || createMutation.isPending || defaults.isPending || defaults.isError}
                        onClick={() => createMutation.mutate()}
                    >
                        Создать проект
                    </Button>
                </div>
            </div>
        </Page>
    );
}
