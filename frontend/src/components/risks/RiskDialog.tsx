import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlignLeft, CalendarClock, Link2, ListChecks, Pencil, Trash2 } from "lucide-react";
import { Tab, TabList, TabPanel, Tabs } from "react-aria-components";
import { api, ApiError, endpoints, queryKeys } from "@/lib/api";
import { formatDateTime, formatFullDate } from "@/lib/dates";
import {
    IMPACT_LABELS, PROBABILITY_LABELS, RISK_STATUS_LABELS, RISK_STRATEGY_LABELS,
    isRiskReviewDue, riskChanges, type ProjectRisk, type RiskInput,
} from "@/lib/risks";
import { fullName, type ProjectMember, type Task } from "@/lib/types";
import { useInvalidateRisks } from "@/lib/useRisks";
import { useRenderedMarkdown } from "@/lib/useRenderedMarkdown";
import { useMediaQuery } from "@/lib/useCalendarView";
import { useUiStore } from "@/stores/ui";
import { useToast } from "@/lib/toast";
import { Button } from "@/components/ui/Button";
import { DrawerSection } from "@/components/ui/Drawer";
import { Modal } from "@/components/ui/Modal";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { RiskBadge } from "@/components/risks/RiskBadge";
import { RiskForm } from "@/components/risks/RiskForm";

const SECTIONS = [
    { id: "overview", label: "Основное", icon: AlignLeft },
    { id: "plans", label: "Планы", icon: ListChecks },
    { id: "control", label: "Контроль", icon: CalendarClock },
    { id: "links", label: "Связи", icon: Link2 },
] as const;
type RiskSection = typeof SECTIONS[number]["id"];
const PANEL_CLASS = "scrollbar-thin min-h-0 min-w-0 flex-1 overflow-y-auto outline-none data-[inert]:hidden data-[focus-visible]:outline-2 data-[focus-visible]:-outline-offset-2 data-[focus-visible]:outline-accent/70";

function Markdown({ text }: { text: string }) {
    const html = useRenderedMarkdown(text);
    return text ? <div className="markdown-body min-w-0 text-[13px] [overflow-wrap:anywhere] [&_pre]:max-w-full [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto [&_table]:[overflow-wrap:normal]" dangerouslySetInnerHTML={{ __html: html }} /> : <p className="text-[12px] text-muted">Пока не заполнено</p>;
}

export function RiskDialog() {
    const selected = useUiStore((state) => state.selectedRisk);
    const setSelected = useUiStore((state) => state.setSelectedRisk);
    return selected ? <RiskDialogContent key={`${selected.projectId}:${selected.riskId}`} {...selected} onClose={() => setSelected(null)} /> : null;
}

function RiskDialogContent({ projectId, riskId, onClose }: { projectId: number; riskId: number; onClose: () => void }) {
    const formId = useId();
    const vertical = useMediaQuery("(min-width: 640px)");
    const [section, setSection] = useState<RiskSection>("overview");
    const [editing, setEditing] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const client = useQueryClient();
    const invalidate = useInvalidateRisks(projectId);
    const toast = useToast();
    const openTask = useUiStore((state) => state.setSelectedTaskId);
    const query = useQuery({
        queryKey: queryKeys.projectRisk(projectId, riskId),
        queryFn: () => api.get<ProjectRisk>(endpoints.projectRisk(projectId, riskId)),
        retry: false,
    });
    const risk = query.error instanceof ApiError && [403, 404].includes(query.error.status) ? undefined : query.data;
    const members = useQuery({
        queryKey: queryKeys.projectMembers(projectId),
        queryFn: () => api.get<ProjectMember[]>(endpoints.projectMembers(projectId)),
    });
    const taskQuery = useQuery({
        // Full TaskDialog also stores attention_view: keep this link preview separate.
        queryKey: [...queryKeys.task(risk?.task_id ?? 0), "risk-link", projectId, riskId],
        queryFn: () => api.get<Task>(endpoints.task(risk!.task_id!)),
        enabled: !!risk?.task_id && section === "links" && !editing,
        retry: false,
    });
    const save = useMutation({
        mutationFn: (input: Partial<Omit<RiskInput, "source">>) => api.patch<ProjectRisk>(endpoints.projectRisk(projectId, riskId), input),
        onSuccess: (risk) => {
            client.setQueryData(queryKeys.projectRisk(projectId, riskId), risk);
            setEditing(false);
            void invalidate();
            toast.success("Риск сохранён");
        },
    });
    const remove = useMutation({
        mutationFn: () => api.delete(endpoints.projectRisk(projectId, riskId)),
        onSuccess: () => {
            onClose();
            client.removeQueries({ queryKey: queryKeys.projectRisk(projectId, riskId), exact: true });
            void invalidate();
            toast.success("Риск удалён");
        },
    });
    const busy = save.isPending || remove.isPending;
    const owner = members.data?.find((member) => member.user.id === risk?.owner_user_id);
    const close = () => { if (!busy) onClose(); };

    return (
        <Modal
            isOpen title={risk ? `Риск ${risk.key}` : "Риск"}
            size="xl" layout="workspace" isDismissable={!busy}
            onOpenChange={(open) => { if (!open) close(); }} closeLabel="Закрыть риск"
            header={risk ? <div className="flex flex-col gap-2">
                <div className="flex items-center gap-3"><span className="font-mono text-[11px] text-muted">{risk.key}</span><RiskBadge level={risk.risk_level} /></div>
                <h2 className="text-[15px] font-semibold leading-snug break-words text-primary [overflow-wrap:anywhere]">{risk.title}</h2>
            </div> : <Skeleton className="h-10 w-48" />}
            footer={risk ? editing ? <>
                <Button disabled={busy} onClick={() => { setEditing(false); save.reset(); }}>Отмена</Button>
                <Button variant="primary" type="submit" form={formId} disabled={busy}>{save.isPending ? "Сохранение…" : "Сохранить"}</Button>
            </> : <>
                <Button variant="destructive" disabled={busy} icon={<Trash2 size={14} />} onClick={() => setConfirmDelete(true)}>Удалить</Button>
                <Button key="edit-risk" disabled={busy} icon={<Pencil size={14} />} onClick={(event) => { event.preventDefault(); setEditing(true); }}>Редактировать</Button>
            </> : undefined}
        >
            {query.isPending && <div role="status" aria-label="Загрузка риска" className="space-y-4 p-5"><Skeleton className="h-24" /><Skeleton className="h-32" /></div>}
            {query.error && <div className="p-5"><ErrorMessage title="Не удалось открыть риск" message={query.error.message} action={<Button onClick={() => void query.refetch()}>Повторить</Button>} /></div>}
            {risk && (editing ? <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto p-5">
                <RiskForm projectId={projectId} initial={risk} formId={formId} isSaving={busy} error={save.error?.message} onSave={(input) => {
                    const changes = riskChanges(risk, input);
                    if (Object.keys(changes).length === 0) setEditing(false);
                    else save.mutate(changes);
                }} />
            </div> : <Tabs selectedKey={section} onSelectionChange={(key) => setSection(key as RiskSection)} orientation={vertical ? "vertical" : "horizontal"} className="flex min-h-0 flex-1 flex-col sm:flex-row">
                <TabList aria-label="Разделы риска" className="scrollbar-thin flex shrink-0 gap-1 overflow-x-auto border-b border-line bg-app/25 p-2 sm:w-48 sm:flex-col sm:overflow-x-hidden sm:overflow-y-auto sm:border-r sm:border-b-0 sm:p-3">
                    {SECTIONS.map(({ id, label, icon: Icon }) => <Tab key={id} id={id} aria-label={label} className="brand-tab flex min-h-10 shrink-0 cursor-pointer items-center gap-2.5 rounded-[var(--radius-control)] border border-transparent px-3 py-2 text-[13px] whitespace-nowrap text-secondary outline-none transition-colors hover:bg-hover data-[selected]:border-line data-[selected]:bg-accent/[0.075] data-[selected]:text-primary data-[focus-visible]:ring-2 data-[focus-visible]:ring-accent">
                        <Icon size={16} aria-hidden="true" className="shrink-0 text-muted" /><span>{label}</span>
                    </Tab>)}
                </TabList>
                <div className="flex min-h-0 min-w-0 flex-1 flex-col">
                <TabPanel id="overview" className={PANEL_CLASS}>
                <DrawerSection title="Оценка и состояние">
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-[12px]">
                        <div><dt className="text-muted">Статус</dt><dd className="mt-1 text-primary">{RISK_STATUS_LABELS[risk.status]}</dd></div>
                        <div><dt className="text-muted">Стратегия</dt><dd className="mt-1 text-primary">{RISK_STRATEGY_LABELS[risk.response_strategy]}</dd></div>
                        <div><dt className="text-muted">Вероятность</dt><dd className="mt-1 text-secondary">{PROBABILITY_LABELS[risk.probability]}</dd></div>
                        <div><dt className="text-muted">Влияние</dt><dd className="mt-1 text-secondary">{IMPACT_LABELS[risk.impact]}</dd></div>
                    </dl>
                </DrawerSection>
                <DrawerSection title="Описание"><Markdown text={risk.description} /></DrawerSection>
                <div className="space-y-1 px-5 pb-5 text-[10px] text-muted">
                    <p>{risk.source === "AI_SUGGESTED" ? "Предложен AI · зарегистрирован человеком" : "Создан вручную"}</p>
                    <p>Создан {formatDateTime(risk.created_at)} · изменён {formatDateTime(risk.updated_at)}</p>
                </div>
                </TabPanel>
                <TabPanel id="plans" className={PANEL_CLASS}>
                <DrawerSection title="План митигации"><Markdown text={risk.mitigation_plan} /></DrawerSection>
                <DrawerSection title="План реагирования"><Markdown text={risk.response_plan} /></DrawerSection>
                </TabPanel>
                <TabPanel id="control" className={PANEL_CLASS}>
                <DrawerSection title="Ответственность и контроль">
                    <dl className="space-y-3 text-[12px]">
                        <div><dt className="text-muted">Ответственный</dt><dd className="mt-1 text-secondary">{owner ? fullName(owner.user) : risk.owner_user_id ? "Участник проекта" : "Не назначен"}</dd></div>
                        <div><dt className="text-muted">Следующий пересмотр</dt><dd className={isRiskReviewDue(risk) ? "mt-1 text-warning" : "mt-1 text-secondary"}>{risk.review_date ? formatFullDate(risk.review_date) : "Дата не назначена"}{isRiskReviewDue(risk) && " · требует контроля"}</dd></div>
                    </dl>
                </DrawerSection>
                </TabPanel>
                <TabPanel id="links" className={PANEL_CLASS}>
                <DrawerSection title="Связанная задача">
                    {risk.task_id ? <Button variant="ghost" className="h-auto max-w-full justify-start py-2 text-left [overflow-wrap:anywhere]" icon={<Link2 size={13} className="shrink-0" />} onClick={() => openTask(risk.task_id)}>{!taskQuery.error && taskQuery.data?.project_id === projectId ? `${taskQuery.data.key} · ${taskQuery.data.title}` : "Открыть связанную задачу"}</Button> : <p className="text-[12px] text-muted">Связь с задачей не задана</p>}
                </DrawerSection>
                </TabPanel>
                </div>
            </Tabs>)}
            {confirmDelete && <Modal
                isOpen title={`Удалить ${risk?.key ?? "риск"}?`}
                description="Риск будет удалён из реестра и аналитики проекта."
                isDismissable={!remove.isPending}
                onOpenChange={(open) => { if (!open && !remove.isPending) { setConfirmDelete(false); remove.reset(); } }}
                footer={<>
                    <Button disabled={remove.isPending} onClick={() => setConfirmDelete(false)}>Отмена</Button>
                    <Button variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate()}>{remove.isPending ? "Удаление…" : "Удалить риск"}</Button>
                </>}
            >
                <p className="text-[13px] text-secondary">{risk?.title}</p>
                {remove.error && <p role="alert" className="mt-3 text-[12px] text-danger">{remove.error.message}</p>}
            </Modal>}
        </Modal>
    );
}
