import { useState } from "react";
import { PROJECT_STATUS_LABELS } from "@/lib/types";
import type { Project, ProjectStatus } from "@/lib/types";
import { PROJECT_KEY_PATTERN, type ProjectFormValues } from "@/lib/projectForm";
import { ProjectIconPicker } from "@/components/projects/ProjectIconPicker";
import { Field, Input, Select, Textarea } from "@/components/ui/Field";

interface ProjectFormProps {
    values: ProjectFormValues;
    onChange: (values: ProjectFormValues) => void;
    /** Код проекта участвует в номерах задач, поэтому у существующего проекта его не меняем. */
    lockKey?: boolean;
    requireDeadlineComment?: boolean;
    disabled?: boolean;
    project?: Project;
    onProjectChange?: (project: Project) => void;
    pendingImage?: File | null;
    onPendingImageChange?: (file: File | null) => void;
}

export function ProjectForm({ values, onChange, lockKey = false, requireDeadlineComment = false, disabled = false, project, onProjectChange, pendingImage, onPendingImageChange }: ProjectFormProps) {
    const [keyTouched, setKeyTouched] = useState(false);
    const keyError =
        keyTouched && values.key !== "" && !PROJECT_KEY_PATTERN.test(values.key)
            ? "Латиница и цифры, от 2 до 10 символов, первая — буква."
            : undefined;

    function update(patch: Partial<ProjectFormValues>) {
        onChange({ ...values, ...patch });
    }

    return (
        <fieldset disabled={disabled} className="flex min-w-0 flex-col gap-4">
            <div className="grid gap-4 sm:grid-cols-[140px_1fr]">
                <Field
                    label="Код"
                    hint={lockKey ? undefined : "Префикс номеров задач: PROJ-142"}
                    error={keyError}
                >
                    {(id) => (
                        <Input
                            id={id}
                            value={values.key}
                            disabled={lockKey}
                            maxLength={10}
                            placeholder="PROJ"
                            className="font-mono uppercase"
                            onBlur={() => setKeyTouched(true)}
                            onChange={(event) =>
                                update({ key: event.target.value.toUpperCase() })
                            }
                        />
                    )}
                </Field>

                <Field label="Название">
                    {(id) => (
                        <Input
                            id={id}
                            value={values.name}
                            placeholder="Агент Арис"
                            onChange={(event) => update({ name: event.target.value })}
                        />
                    )}
                </Field>
            </div>

            {([
                ["problem", "Проблема", "Что сейчас не устраивает и почему появился этот проект?"],
                ["goal", "Цель", "Что должно измениться благодаря проекту?"],
                ["expected_result", "Ожидаемый результат", "Что будет готово и как мы поймём, что проект завершён?"],
                ["additional", "Дополнительно", "Ограничения, ссылки и договорённости — по желанию."],
            ] as const).map(([field, label, hint]) => (
                <Field key={field} label={label} hint={hint}>
                    {(id) => (
                        <Textarea id={id} rows={2} value={values.description_sections[field]}
                            onChange={(event) => update({ description_sections: {
                                ...values.description_sections, [field]: event.target.value,
                            } })} />
                    )}
                </Field>
            ))}

            <div className="grid gap-4 sm:grid-cols-3">
                <Field label="Статус">
                    {(id) => (
                        <Select
                            id={id}
                            value={values.status}
                            onChange={(event) =>
                                update({ status: event.target.value as ProjectStatus })
                            }
                        >
                            {Object.entries(PROJECT_STATUS_LABELS).map(([value, label]) => (
                                <option key={value} value={value}>
                                    {label}
                                </option>
                            ))}
                        </Select>
                    )}
                </Field>

                <Field label="Старт">
                    {(id) => (
                        <Input
                            id={id}
                            type="date"
                            value={values.start_date}
                            onChange={(event) => update({ start_date: event.target.value })}
                        />
                    )}
                </Field>

                <Field label="Плановое завершение" hint="Можно оставить без даты">
                    {(id) => (
                        <Input
                            id={id}
                            type="date"
                            value={values.due_date}
                            min={values.start_date || undefined}
                            onChange={(event) => update({ due_date: event.target.value })}
                        />
                    )}
                </Field>
            </div>

            {values.start_date && values.due_date && values.due_date < values.start_date && (
                <p role="alert" className="text-[12px] text-danger">Окончание проекта не может быть раньше начала.</p>
            )}
            {requireDeadlineComment && (
                <Field label="Причина изменения срока" hint="Обязательно. Сохраним причину и обе даты в истории проекта.">
                    {(id) => <Textarea id={id} required rows={2} maxLength={5000}
                        value={values.due_date_comment}
                        onChange={(event) => update({ due_date_comment: event.target.value })} />}
                </Field>
            )}

            <Field label="Оформление проекта">{() => <ProjectIconPicker value={values.icon} color={values.color}
                onChange={appearance => update(appearance)} disabled={disabled} project={project}
                onProjectChange={onProjectChange} pendingImage={pendingImage} onPendingImageChange={onPendingImageChange} />}</Field>
        </fieldset>
    );
}
