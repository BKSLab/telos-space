import type { RiskSummary } from "@/lib/risks";
import type { TaskChecklist } from "@/lib/checklists";

export interface UserSummary {
    id: number;
    username: string;
    last_name: string;
    first_name: string;
    middle_name: string | null;
    has_avatar: boolean;
}

export interface User extends UserSummary {
    email: string | null;
    phone: string | null;
    telegram: string | null;
    created_at: string;
}

/** Регистрация собирает минимум: остальное заполняется в профиле. */
export interface RegisterPayload {
    username: string;
    password: string;
    password_confirm: string;
    last_name: string;
    first_name: string;
    invite_code: string;
}

export interface LoginPayload {
    username: string;
    password: string;
}

export interface UserUpdatePayload {
    last_name?: string;
    first_name?: string;
    middle_name?: string | null;
    email?: string | null;
    phone?: string | null;
    telegram?: string | null;
}

export interface PasswordChangePayload {
    current_password: string;
    password: string;
    password_confirm: string;
}

export type ProjectStatus = "PLANNING" | "ACTIVE" | "PAUSED" | "COMPLETED" | "ARCHIVED";

export type TaskPriority = "LOW" | "MEDIUM" | "HIGH" | "URGENT";

export type TaskRole = "PM" | "BE" | "FE" | "UXR" | "UXD" | "EXPERT" | "QA" | "BA" | "MKT";

export type ProjectRole = "OWNER" | "MEMBER";

export interface ProjectMember {
    id: number;
    project_id: number;
    role: ProjectRole;
    user: UserSummary;
    created_at: string;
}

export type TaskParticipantRole = "EXECUTOR" | "REPORTER" | "OBSERVER";

export interface TaskParticipant {
    id: number;
    role: TaskParticipantRole;
    user: UserSummary;
}

export interface ProjectDescription {
    problem: string;
    goal: string;
    expected_result: string;
    additional: string;
}

export interface ProjectDeadlineChange {
    id: number;
    previous_due_date: string | null;
    new_due_date: string | null;
    changed_by_user_id: number | null;
    changed_by_name: string;
    comment: string | null;
    created_at: string;
}

export interface Project {
    id: number;
    owner_id?: number | null;
    description_sections?: ProjectDescription | null;
    due_date_has_been_set?: boolean;
    key: string;
    name: string;
    description_md: string | null;
    status: ProjectStatus;
    color: string;
    icon: string | null;
    icon_image_version?: string | null;
    start_date: string | null;
    due_date: string | null;
    order_index: number;
    created_at: string;
    updated_at: string;
    my_role?: EffectiveProjectRole;
    is_member?: boolean;
}

export interface ProjectCreate {
    key: string;
    name: string;
    description_md?: string | null;
    description_sections?: ProjectDescription | null;
    member_logins?: string[];
    stages?: Pick<ProjectStage, "name" | "color" | "is_done_stage">[];
    status?: ProjectStatus;
    color?: string;
    icon?: string | null;
    start_date?: string | null;
    due_date?: string | null;
    member_usernames?: string[];
}

export type ProjectUpdate = Partial<ProjectCreate> & {
    order_index?: number;
    due_date_comment?: string;
    expected_due_date?: string | null;
};

export interface StageBreakdown {
    stage_id: number;
    stage_name: string;
    color: string;
    is_done_stage: boolean;
    tasks_count: number;
}

export interface ProjectStats {
    project_id: number;
    total_tasks: number;
    done_tasks: number;
    in_progress_tasks: number;
    overdue_tasks: number;
    due_soon_tasks: number;
    unassigned_tasks: number;
    completion_rate: number;
    next_due_date: string | null;
    stage_breakdown: StageBreakdown[];
}

export interface ProjectStage {
    id: number;
    project_id: number;
    name: string;
    order_index: number;
    color: string;
    is_done_stage: boolean;
}

export interface StageCreate {
    name: string;
    color?: string;
    is_done_stage?: boolean;
}

export interface StageUpdate {
    name?: string;
    color?: string;
    order_index?: number;
    is_done_stage?: boolean;
}

export type SearchMatchSource =
    | "title"
    | "description"
    | "comment"
    | "comment_author"
    | "content"
    | "slug"
    | null;

export interface Task {
    revision?: number;
    checklist?: TaskChecklist | null;
    checklist_revision?: number;
    id: number;
    kind?: TaskKind;
    project_id: number | null;
    stage_id: number | null;
    status?: TaskStatus | null;
    number: number;
    key: string;
    title: string;
    description_md: string | null;
    priority: TaskPriority;
    role: TaskRole | null;
    assignee: string | null;
    participants: TaskParticipant[];
    start_date: string | null;
    due_date: string | null;
    baseline_start_date: string | null;
    baseline_due_date: string | null;
    completed_at: string | null;
    position: number;
    created_at: string;
    updated_at: string;
    comments_count: number;
    last_comment: string | null;
    search_match_source: SearchMatchSource;
    search_title: string | null;
    search_excerpt: string | null;
    wbs_node_id: number | null;
}

export interface TaskCompact {
    checklist?: TaskChecklist | null;
    id: number;
    key: string;
    title: string;
    stage_id: number;
    priority: TaskPriority;
    assignee: string | null;
    start_date: string | null;
    due_date: string | null;
    is_done: boolean;
    wbs_node_id: number | null;
    wbs_position: number | null;
    canvas_x: number | null;
    canvas_y: number | null;
}

export interface CalendarRange {
    date_from: string;
    date_to: string;
    today: string;
}

export interface CalendarProject {
    start_date: string | null;
    due_date: string | null;
}

export interface CalendarStage {
    id: number;
    name: string;
    color: string;
    order_index: number;
    is_done_stage: boolean;
}

export interface CalendarWbsNode {
    id: number;
    parent_id: number | null;
    title: string;
    position: number;
}

export interface CalendarTask {
    id: number;
    key: string;
    title: string;
    start_date: string | null;
    due_date: string | null;
    baseline_start_date: string | null;
    baseline_due_date: string | null;
    drift_days: number | null;
    stage_id: number;
    priority: TaskPriority;
    assignee: string | null;
    is_done: boolean;
    is_overdue: boolean;
    is_due_soon: boolean;
    risk_level: "low" | "medium" | "high" | null;
    risk_reasons: CalendarRiskReason[];
    updated_at: string;
    progress?: number | null;
    is_critical?: boolean;
    wbs_node_id: number | null;
}

export interface CalendarRiskReason {
    code: string;
    message: string;
    days: number | null;
    task_key?: string | null;
    milestone_title?: string | null;
}

export interface CalendarDateChange {
    id: number;
    task_id: number;
    task_key: string;
    task_title: string;
    from_date: string | null;
    to_date: string | null;
    changed_at: string;
}

export interface CalendarSummary {
    overdue: number;
    due_soon: number;
    unscheduled: number;
    drifted: number;
    dependency_risks: number;
}

export interface TaskDependency {
    id: number;
    project_id: number;
    predecessor_task_id: number;
    successor_task_id: number;
    dependency_type: "FINISH_TO_START";
    lag_days: number;
    created_at: string;
    is_critical?: boolean;
}

export interface TaskDependencyInput {
    predecessor_task_id: number;
    successor_task_id: number;
    dependency_type: "FINISH_TO_START";
    lag_days: number;
}

export interface ScenarioTaskDates {
    start_date: string | null;
    due_date: string | null;
}

export interface ScenarioChangeInput extends ScenarioTaskDates {
    task_id: number;
}

export interface ScenarioNormalizedChange {
    task_id: number;
    task_key: string;
    task_title: string;
    current: ScenarioTaskDates;
    proposed: ScenarioTaskDates;
    expected_updated_at: string;
    source: "DIRECT" | "CASCADE";
    reasons: CalendarRiskReason[];
}

export interface ScenarioConflict {
    code: string;
    message: string;
    task_id: number;
    task_key: string;
}

export interface ScenarioPreview {
    changes: ScenarioNormalizedChange[];
    conflicts: ScenarioConflict[];
    consequences_count: number;
    can_apply: boolean;
}

export interface ScenarioApplyResult {
    applied_count: number;
    task_ids: number[];
}

export type ProjectMilestoneStatus = "PLANNED" | "ACHIEVED";

export interface ProjectMilestone {
    id: number;
    project_id: number;
    title: string;
    due_date: string;
    status: ProjectMilestoneStatus;
    description_md: string | null;
    created_at: string;
    updated_at: string;
    wbs_node_id: number | null;
}

export interface ProjectMilestoneInput {
    title: string;
    due_date: string;
    status: ProjectMilestoneStatus;
    description_md: string | null;
    wbs_node_id: number | null;
}

export interface CalendarMilestone {
    id: number | null;
    title: string;
    due_date: string;
    status: ProjectMilestoneStatus;
    description_md: string | null;
    is_system: boolean;
    wbs_node_id: number | null;
}

export interface ProjectCalendar {
    range: CalendarRange;
    project: CalendarProject;
    tasks: CalendarTask[];
    stages: CalendarStage[];
    assignees: string[];
    summary: CalendarSummary;
    recent_changes: CalendarDateChange[];
    milestones: CalendarMilestone[];
    dependencies: TaskDependency[];
    wbs_nodes: CalendarWbsNode[];
}

export interface UnscheduledTasksPage {
    items: CalendarTask[];
    next_cursor: number | null;
}

export interface TaskCreate {
    title: string;
    checklist?: TaskChecklist | null;
    description_md?: string | null;
    stage_id?: number | null;
    priority?: TaskPriority;
    role?: TaskRole | null;
    assignee?: string | null;
    executor_id?: number | null;
    reporter_id?: number | null;
    observer_ids?: number[];
    start_date?: string | null;
    due_date?: string | null;
    wbs_node_id?: number | null;
}

export interface TaskUpdate {
    expected_revision?: number;
    title?: string;
    checklist?: TaskChecklist | null;
    checklist_revision?: number;
    description_md?: string | null;
    priority?: TaskPriority;
    role?: TaskRole | null;
    assignee?: string | null;
    executor_id?: number | null;
    reporter_id?: number | null;
    observer_ids?: number[] | null;
    start_date?: string | null;
    due_date?: string | null;
}

export type TaskActivityEventType =
    | "CHECKLIST_CHANGED"
    | "STAGE_CHANGED"
    | "DUE_DATE_CHANGED"
    | "START_DATE_CHANGED"
    | "BASELINE_CHANGED"
    | "DESCRIPTION_CHANGED"
    | "PRIORITY_CHANGED"
    | "ASSIGNEE_CHANGED"
    | "COMMENT_ADDED"
    | "TASK_MOVED"
    | "LINKS_REMOVED" | "WBS_NODE_CHANGED";

export interface TaskActivity {
    actor_user_id: number | null;
    actor_name: string | null;
    via_api_key_label: string | null;
    id: number;
    task_id: number;
    event_type: TaskActivityEventType;
    from_value: string | null;
    to_value: string | null;
    created_at: string;
}

export interface TaskComment {
    id: number;
    task_id: number;
    author?: UserSummary | null;
    author_name: string | null;
    body_md: string;
    created_at: string;
    attachments?: TaskAttachment[];
}

export interface TaskAttachment {
    id: number;
    task_id: number;
    original_name: string;
    content_type: string;
    size: number;
    created_at: string;
    content_url: string;
    previewable: boolean;
    preview_url?: string | null;
    caption?: string | null;
    uploaded_by_user_id?: number | null;
    extraction_status?: FileExtractionStatus;
    extraction_detail?: string | null;
    fts_status?: "pending" | "ready" | "failed";
    fts_detail?: string | null;
    index_status?: FileIndexStatus;
    index_detail?: string | null;
}

export interface TaskRephraseResult {
    description_md: string;
}

export interface WbsNode {
    id: number;
    project_id: number;
    parent_id: number | null;
    title: string;
    position: number;
    created_at: string;
    updated_at: string;
}

export interface WbsStats {
    total_nodes: number;
    total_tasks: number;
    assigned_tasks: number;
    unassigned_tasks: number;
    done_tasks: number;
    overdue_tasks: number;
}

export interface WbsStructure {
    nodes: WbsNode[];
    tasks: TaskCompact[];
    stats: WbsStats;
}

export interface WbsNodeDeleteResult {
    deleted_nodes: number;
    released_tasks: number;
}

/** Раздел черновика ИСР: настоящего идентификатора у него ещё нет. */
export interface WbsSuggestedNode {
    temp_id: string;
    parent_temp_id: string | null;
    title: string;
    rationale: string | null;
}

export interface WbsSuggestedAssignment {
    task_id: number;
    node_temp_id: string;
}

export interface WbsSuggestion {
    nodes: WbsSuggestedNode[];
    assignments: WbsSuggestedAssignment[];
    summary: string;
    skipped_task_ids: number[];
}

export interface WbsSuggestionApplyResult {
    created_nodes: number;
    assigned_tasks: number;
}

export interface DocumentListItem {
    id: number;
    project_id: number;
    slug: string;
    title: string;
    updated_at: string;
    search_match_source: SearchMatchSource;
    search_title: string | null;
    search_excerpt: string | null;
}

export interface DocumentDetail extends DocumentListItem {
    content_md: string;
    created_at: string;
}

export type FileExtractionStatus = "pending" | "processing" | "ready" | "empty" | "unsupported" | "failed" | "disabled";
export type FileIndexStatus = "pending" | "processing" | "ready" | "failed" | "disabled";

export interface ProjectFile {
    id: number;
    project_id: number;
    uploaded_by_user_id: number | null;
    uploaded_by_name?: string | null;
    original_name: string;
    content_type: string;
    size: number;
    created_at: string;
    content_url: string;
    extraction_status: FileExtractionStatus;
    extraction_detail: string | null;
    index_status: FileIndexStatus;
    index_detail: string | null;
    indexed_at: string | null;
    summary: string | null;
    caption?: string | null;
    previewable?: boolean;
    preview_url?: string | null;
    fts_status?: "pending" | "ready" | "failed";
    fts_detail?: string | null;
}

export type FileSearchScope = "all" | "project_files" | "task_files" | "documents";

export interface FileSearchItem {
    source_type: "project_file" | "task_attachment" | "document";
    source_id: number;
    project_id: number | null;
    task_id: number | null;
    task_key: string | null;
    task_title: string | null;
    document_slug: string | null;
    title: string;
    caption: string | null;
    content_type: string | null;
    preview_url: string | null;
    excerpt: string;
    rank: number;
}

export interface FileSearchPage {
    items: FileSearchItem[];
    total: number;
    limit: number;
    offset: number;
}

export interface ProjectFileDetail extends ProjectFile {
    can_edit?: boolean;
    text: string;
    text_total_chars: number;
    text_offset: number;
    next_offset: number | null;
}

export interface LinkedDocument {
    link_id: number;
    document_id: number;
    slug: string;
    title: string;
}

export interface DocumentLink {
    id: number;
    document_id: number;
    task_id: number;
}

export interface TaskDocumentImport {
    attachment: TaskAttachment;
    document: DocumentDetail;
    link: DocumentLink;
}

export interface LinkedTask {
    link_id: number;
    task_id: number;
    key: string;
    title: string;
}

export type KnowledgeEntityType =
    | "project"
    | "task"
    | "document"
    | "project_file"
    | "comment"
    | "attachment"
    | "milestone"
    | "risk"
    | "stage"
    | "sticker"
    | "member"
    | "activity"
    | "deadline_change"
    | "task_report"
    | "analytics_report" | "wbs_node";

export interface KnowledgeSource {
    card?: { key?: string | null; status?: string | null; priority?: string | null; assignee?: string | null; due_date?: string | null; summary?: string | null; login?: string | null; avatar_url?: string | null; role?: string | null } | null;
    source_id: string;
    entity_type: KnowledgeEntityType;
    entity_id: number;
    title: string;
    excerpt: string | null;
    score: number | null;
    task_id: number | null;
    document_slug: string | null;
    related_source_ids?: string[];
}

export interface KnowledgeAnswer {
    answer: string;
    sources: KnowledgeSource[];
}

export interface KnowledgeStatus {
    enabled: boolean;
    ready: boolean;
    points_count: number | null;
    pending_jobs: number;
    processing_jobs: number;
    failed_jobs: number;
    last_error: string | null;
    coverage: { entity_type: KnowledgeEntityType; total: number; indexed: number; missing: number; stale: number }[];
    file_issues: { source_id: string; title: string; status: string; detail: string | null }[];
    obsolete_points: number;
}

export interface KnowledgeChatMessage {
    role: "user" | "assistant";
    content: string;
}

export interface KnowledgeAskPayload {
    question: string;
    history: KnowledgeChatMessage[];
}

export interface AgentConversation {
    id: number;
    title: string;
    created_at: string;
    updated_at: string;
    project_id: number;
}

export interface AgentConversationPage {
    items: AgentConversation[];
    next_offset: number | null;
}

export interface AgentToolRun {
    id: string;
    tool_name: string;
    title: string;
    arguments: Record<string, unknown>;
    status: "pending" | "completed" | "rejected" | "failed";
    result: Record<string, unknown>;
    created_at: string;
}

export interface AgentMessage {
    actions?: AgentToolRun[];
    files?: AgentFile[];
    id: number;
    conversation_id: number;
    request_id: string;
    role: "user" | "assistant";
    content: string;
    sources: KnowledgeSource[];
    page_context?: TimPageContext | null;
    status: "queued" | "processing" | "completed" | "failed";
    error: string | null;
    progress?: string | null;
    created_at: string;
}

export interface AgentFile {
    id: string;
    original_name: string;
    content_type: string;
    size: number;
    created_at: string;
}

export interface AgentMessagePage {
    items: AgentMessage[];
    next_before_id: number | null;
}

export interface AgentMessageAccepted {
    user_message: AgentMessage;
    assistant_message: AgentMessage;
}

export interface DashboardTotals {
    total_projects: number;
    active_projects: number;
    total_tasks: number;
    done_tasks: number;
    in_progress_tasks: number;
    overdue_tasks: number;
    completion_rate: number;
}

export interface DashboardProject {
    risks?: RiskSummary;
    id: number;
    key: string;
    name: string;
    description_md: string | null;
    status: ProjectStatus;
    color: string;
    icon: string | null;
    icon_image_version?: string | null;
    total_tasks: number;
    done_tasks: number;
    in_progress_tasks: number;
    overdue_tasks: number;
    completion_rate: number;
    next_due_date: string | null;
    updated_at: string;
    my_role?: EffectiveProjectRole;
    is_member?: boolean;
}

export interface DashboardTask {
    id: number;
    key: string;
    title: string;
    project_id: number;
    project_key: string;
    project_name: string;
    project_color: string;
    stage_id: number;
    stage_name: string;
    priority: TaskPriority;
    due_date: string | null;
    is_overdue: boolean;
    updated_at: string;
}

export interface Dashboard {
    scope?: DashboardScope;
    has_outside_tasks?: boolean;
    totals: DashboardTotals;
    projects: DashboardProject[];
    attention_tasks: DashboardTask[];
    recent_tasks: DashboardTask[];
}

export const PROJECT_STATUS_LABELS: Record<ProjectStatus, string> = {
    PLANNING: "Планирование",
    ACTIVE: "В работе",
    PAUSED: "На паузе",
    COMPLETED: "Завершён",
    ARCHIVED: "В архиве",
};

export const PRIORITY_LABELS: Record<TaskPriority, string> = {
    LOW: "Низкий",
    MEDIUM: "Средний",
    HIGH: "Высокий",
    URGENT: "Срочный",
};

export const PRIORITY_ORDER: TaskPriority[] = ["URGENT", "HIGH", "MEDIUM", "LOW"];

export const ROLE_LABELS: Record<TaskRole, string> = {
    PM: "Менеджер проекта",
    BE: "Backend",
    FE: "Frontend",
    UXR: "UX-исследования",
    UXD: "UX-дизайн",
    EXPERT: "Эксперт",
    QA: "Тестирование",
    BA: "Аналитика",
    MKT: "Маркетинг",
};

/** Полное имя: отчество есть не у всех, поэтому склеиваем через фильтр. */
export function fullName(user: UserSummary): string {
    return [user.last_name, user.first_name, user.middle_name].filter(Boolean).join(" ");
}

/** Инициалы для аватара-заглушки. */
export function initials(user: UserSummary): string {
    return `${user.last_name.charAt(0)}${user.first_name.charAt(0)}`.toUpperCase();
}

export const PROJECT_COLORS = [
    "#58a6ff",
    "#a371f7",
    "#3fb950",
    "#d29922",
    "#f85149",
    "#39c5cf",
    "#db61a2",
    "#7d8793",
];

export type ApiTokenScope = "READ" | "WRITE";

export interface ApiToken {
    id: number;
    name: string;
    prefix: string;
    scope: ApiTokenScope;
    created_at: string;
    expires_at: string | null;
    revoked_at: string | null;
    last_used_at: string | null;
}

export interface ApiTokenCreatePayload {
    name: string;
    scope: ApiTokenScope;
    ttl_days: number | null;
}

export interface ApiTokenCreated {
    token: ApiToken;
    secret: string;
}

export type AnalyticsScope = "PORTFOLIO" | "PROJECT";
export type AnalyticsHealth = "STABLE" | "WATCH" | "RISK" | "CRITICAL";
export type AnalyticsSeverity = "HIGH" | "MEDIUM" | "LOW";
export type AnalyticsFindingKind = "OVERDUE" | "RISK" | "BLOCKER" | "PROCESS" | "DATA_GAP";
export type AnalyticsHorizon = "TODAY" | "WEEK" | "LATER";

export interface AnalyticsTaskRef {
    id: number;
    key: string;
    title: string;
    project_key: string;
    due_date: string | null;
    is_overdue: boolean;
}

export interface AnalyticsFinding {
    kind: AnalyticsFindingKind;
    severity: AnalyticsSeverity;
    title: string;
    detail: string;
    project_key: string | null;
    project_name: string | null;
    tasks: AnalyticsTaskRef[];
}

export interface AnalyticsProgress {
    title: string;
    detail: string;
    project_key: string | null;
    project_name: string | null;
    tasks: AnalyticsTaskRef[];
}

export interface AnalyticsRecommendation {
    horizon: AnalyticsHorizon;
    title: string;
    detail: string;
    project_key: string | null;
    project_name: string | null;
    tasks: AnalyticsTaskRef[];
}

export interface AnalyticsSignals extends Partial<RiskSummary> {
    total_tasks: number;
    done_tasks: number;
    overdue_tasks: number;
    due_soon_tasks: number;
    no_due_date_tasks: number;
    unassigned_tasks: number;
    stale_tasks: number;
    blocked_tasks: number;
    milestones_at_risk: number;
    unplaced_tasks: number;
}

export interface AnalyticsContext {
    entity_counts?: Record<string, { total: number; included: number }>;
    risks_total?: number;
    risks_included?: number;
    projects: number;
    tasks_total: number;
    tasks_included: number;
    comments_included: number;
    documents_included: number;
    stickers_included: number;
    milestones_included: number;
    activity_included: number;
    truncated: boolean;
    omitted: string[];
    wbs_nodes_included: number;
}

export interface AnalyticsReport {
    id: number;
    scope: AnalyticsScope;
    project_id: number | null;
    project_key: string | null;
    project_name: string | null;
    created_at: string;
    created_by: string;
    llm_model: string;
    duration_ms: number;
    headline: string;
    health: AnalyticsHealth;
    health_note: string;
    findings: AnalyticsFinding[];
    progress: AnalyticsProgress[];
    recommendations: AnalyticsRecommendation[];
    signals: AnalyticsSignals;
    context: AnalyticsContext;
}

export const ANALYTICS_HEALTH_LABELS: Record<AnalyticsHealth, string> = {
    STABLE: "Стабильно",
    WATCH: "Под наблюдением",
    RISK: "Есть риск",
    CRITICAL: "Критично",
};

export const ANALYTICS_FINDING_LABELS: Record<AnalyticsFindingKind, string> = {
    OVERDUE: "Просрочка",
    RISK: "Риск",
    BLOCKER: "Блокер",
    PROCESS: "Процесс",
    DATA_GAP: "Пробел в данных",
};

export const ANALYTICS_HORIZON_LABELS: Record<AnalyticsHorizon, string> = {
    TODAY: "Сегодня",
    WEEK: "На неделе",
    LATER: "Позже",
};


export type EffectiveProjectRole = ProjectRole;

export type TaskKind = "PROJECT" | "PERSONAL";

export type TaskStatus = "NEW" | "IN_PROGRESS" | "REVIEW" | "DONE" | "CANCELLED";

export interface ProjectBaselineResult {
    dry_run: boolean;
    scheduled_count: number;
    changed_count: number;
    task_ids: number[];
}

export interface CalendarTaskSource {
    icon_image_version?: string | null;
    kind: TaskKind;
    id: number | null;
    key: string | null;
    name: string | null;
    color: string | null;
    icon?: string | null;
}

export type CalendarGridTask = Omit<CalendarTask, "stage_id"> & {
    stage_id: number | null;
    status?: TaskStatus | null;
    /** «Мой календарь»: состояние как в «Моих задачах» (у проектной — по стадии) и его подпись. */
    state?: TaskStatus | null;
    state_label?: string | null;
    source?: CalendarTaskSource;
};

export interface CalendarProjectRef {
    id: number;
    key: string;
    name: string;
    color: string;
}

export type CalendarGridMilestone = CalendarMilestone & { project?: CalendarProjectRef };

export type MyCalendarTask = CalendarGridTask & { source: CalendarTaskSource };

export interface MyCalendar {
    range?: CalendarRange;
    tasks: MyCalendarTask[];
    milestones: CalendarGridMilestone[];
    summary?: { overdue: number; due_soon: number; unscheduled: number };
    truncated?: boolean;
}

export interface MyUnscheduledTasksPage {
    items: MyCalendarTask[];
    next_cursor: number | null;
}

export interface PortfolioMilestone {
    id: number | null;
    title: string;
    due_date: string;
    status: ProjectMilestoneStatus;
    is_system: boolean;
}

export interface PortfolioCalendarProject {
    id: number;
    key: string;
    name: string;
    color: string;
    icon: string | null;
    icon_image_version?: string | null;
    status: ProjectStatus;
    start_date: string | null;
    due_date: string | null;
    is_unscheduled?: boolean;
    completion_rate: number;
    total_tasks: number;
    overdue_tasks: number;
    milestones: PortfolioMilestone[];
}

export interface PortfolioCalendar {
    projects: PortfolioCalendarProject[];
}

export type HolidayKind = "HOLIDAY" | "DAY_OFF" | "WORKING_DAY" | "SHORT_DAY";

export interface CalendarHoliday {
    date: string;
    kind: HolidayKind;
    title: string | null;
}

export interface CalendarHolidays {
    year: number;
    known: boolean;
    source?: string | null;
    days: CalendarHoliday[];
}

export interface IcsTokenStatus {
    exists: boolean;
    created_at?: string | null;
    last_used_at?: string | null;
    url?: string | null;
}

export interface IcsTokenCreated extends IcsTokenStatus {
    url: string;
}

export interface PersonalTaskCreate {
    title: string;
    description_md?: string | null;
    priority?: TaskPriority;
    start_date?: string | null;
    due_date?: string | null;
    checklist?: TaskChecklist | null;
    executor_id?: number | null;
    observer_ids?: number[];
}

export type MyTaskRoleFilter = "all" | "doing" | "assigned" | "watching";

export interface MyTaskContext {
    kind: "PROJECT";
    id: number;
    key: string;
    name: string;
    color?: string | null;
    icon?: string | null;
    icon_image_version?: string | null;
}

export interface MyTask extends Task {
    context: MyTaskContext | null;
    state: TaskStatus;
    state_label: string;
    my_roles: TaskParticipantRole[];
}

export interface BoardObjectSelectionRef {
    type: "shape" | "sticker";
    id: number
}

export interface TimPageContext {
    board_selection?: BoardObjectSelectionRef[];
    board_id?: number | null;
    route: string;
    project_key: string | null;
    task_id: number | null;
}

export interface AgentPendingAction {
    id: string;
    tool_name: string;
    title: string;
    status: "pending" | "completed" | "rejected" | "failed" | "expired";
    arguments: Record<string, unknown>;
    preview: Record<string, unknown>;
    result: Record<string, unknown>;
    project: { id: number; key: string; name: string };
    created_at: string;
    expires_at: string;
}

export type DashboardScope = "mine" | "all";

export type TaskMoveTarget =
    | { project_id: number; stage_id?: number | null; remove_outsiders?: boolean }
    | { personal: true };

export interface TaskMoveLosses {
    wbs_placements: number;
    dependencies: number;
    document_links: number;
    risks: number;
    board_stickers: number;
}

export interface TaskMovePreview {
    from_key: string;
    target_kind: TaskKind;
    target_name: string;
    target_key: string;
    stage_id: number | null;
    stage_name: string | null;
    status: TaskStatus | null;
    executor: UserSummary | null;
    losses: TaskMoveLosses;
    outsiders: UserSummary[];
    notes: string[];
}

export interface TaskMoveResult {
    task: Task;
    from_key: string;
    to_key: string;
    losses: TaskMoveLosses;
    notes: string[];
}
