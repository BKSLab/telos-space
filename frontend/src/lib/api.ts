const API_BASE_URL = import.meta.env.DEV ? "http://localhost:8000" : "";

export function apiUrl(path: string): string {
    return `${API_BASE_URL}${path}`;
}

export class ApiError extends Error {
    status: number;

    constructor(status: number, message: string) {
        super(message);
        this.status = status;
    }
}

/** Слушатели, которым нужно узнать о протухшей сессии. */
const unauthorizedHandlers = new Set<() => void>();

export function onUnauthorized(handler: () => void): () => void {
    unauthorizedHandlers.add(handler);
    return () => unauthorizedHandlers.delete(handler);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const isFormData = init?.body instanceof FormData;
    const response = await fetch(apiUrl(path), {
        ...init,
        // Сессия живёт в httpOnly cookie, поэтому её нужно слать с каждым запросом.
        credentials: "include",
        headers: {
            ...(!isFormData && { "Content-Type": "application/json" }),
            ...init?.headers,
        },
    });

    if (response.status === 401) {
        // Токен протух в открытой вкладке: сообщаем приложению, чтобы увело на вход.
        for (const handler of unauthorizedHandlers) {
            handler();
        }
    }

    if (!response.ok) {
        const body = await response.json().catch(() => null);
        const message = body?.detail
            ? typeof body.detail === "string"
                ? body.detail
                : JSON.stringify(body.detail)
            : response.statusText;
        throw new ApiError(response.status, message);
    }

    if (response.status === 204) {
        return undefined as T;
    }

    return response.json() as Promise<T>;
}

export const api = {
    get: <T>(path: string) => request<T>(path),
    post: <T>(path: string, body?: unknown) =>
        request<T>(path, {
            method: "POST",
            body: body !== undefined ? JSON.stringify(body) : undefined,
        }),
    postForm: <T>(path: string, body: FormData) => request<T>(path, { method: "POST", body }),
    put: <T>(path: string, body: unknown) => request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
    patch: <T>(path: string, body: unknown) =>
        request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
    delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

const V1 = "/api/v1";

export const authEndpoints = {
    register: () => `${V1}/auth/register`,
    login: () => `${V1}/auth/login`,
    logout: () => `${V1}/auth/logout`,
    me: () => `${V1}/auth/me`,
    profile: () => `${V1}/users/me`,
    password: () => `${V1}/users/me/password`,
    avatar: () => `${V1}/users/me/avatar`,
    apiTokens: () => `${V1}/users/me/tokens`,
    apiToken: (tokenId: number) => `${V1}/users/me/tokens/${tokenId}`,
};

/** Единый источник правды по адресам API — маршруты не разъезжаются по экранам. */
export const endpoints = {
    taskChecklistSuggestion: (projectId: number) => `${V1}/projects/${projectId}/tasks/checklist-suggestion`,
    dashboard: (scope: "mine" | "all" = "mine") => `${V1}/dashboard${scope === "all" ? "?scope=all" : ""}`,
    dashboardAnalytics: (projectId?: number | null) =>
        `${V1}/dashboard/analytics${projectId ? `?project_id=${projectId}` : ""}`,
    projects: () => `${V1}/projects`,
    projectDefaults: () => `${V1}/projects/defaults`,
    projectDeadlineHistory: (projectId: number) => `${V1}/projects/${projectId}/deadline-history`,
    project: (projectId: number) => `${V1}/projects/${projectId}`,
    projectStats: (projectId: number) => `${V1}/projects/${projectId}/stats`,
    projectRisks: (projectId: number) => `${V1}/projects/${projectId}/risks`,
    projectRisk: (projectId: number, riskId: number) => `${V1}/projects/${projectId}/risks/${riskId}`,
    projectRiskSummary: (projectId: number) => `${V1}/projects/${projectId}/risks/summary`,
    projectRiskTaskCounts: (projectId: number) => `${V1}/projects/${projectId}/risks/task-counts`,
    projectRiskSuggestions: (projectId: number) => `${V1}/projects/${projectId}/risks/suggestions`,
    projectRiskFieldSuggestion: (projectId: number) => `${V1}/projects/${projectId}/risks/field-suggestion`,
    projectMembers: (projectId: number) => `${V1}/projects/${projectId}/members`,
    projectMember: (projectId: number, userId: number) =>
        `${V1}/projects/${projectId}/members/${userId}`,
    projectMemberAvatar: (projectId: number, userId: number) =>
        `${V1}/projects/${projectId}/members/${userId}/avatar`,
    projectStickers: (projectId: number) => `${V1}/projects/${projectId}/board/stickers`,
    projectSticker: (projectId: number, stickerId: number) =>
        `${V1}/projects/${projectId}/board/stickers/${stickerId}`,
    projectStickerPosition: (projectId: number, stickerId: number) =>
        `${V1}/projects/${projectId}/board/stickers/${stickerId}/position`,
    projectKnowledgeStatus: (projectId: number) =>
        `${V1}/projects/${projectId}/knowledge/status`,
    projectKnowledgeAsk: (projectId: number) => `${V1}/projects/${projectId}/knowledge/ask`,
    agentConversations: (projectId: number) => `${V1}/projects/${projectId}/agent/conversations`,
    agentMessages: (projectId: number, conversationId: number) =>
        `${V1}/projects/${projectId}/agent/conversations/${conversationId}/messages`,
    agentActionDecision: (projectId: number, conversationId: number, actionId: string) =>
        `${V1}/projects/${projectId}/agent/conversations/${conversationId}/actions/${actionId}/decision`,
    agentFiles: (projectId: number, conversationId: number) =>
        `${V1}/projects/${projectId}/agent/conversations/${conversationId}/files`,
    agentMessageRetry: (projectId: number, conversationId: number, messageId: number) =>
        `${V1}/projects/${projectId}/agent/conversations/${conversationId}/messages/${messageId}/retry`,
    projectKnowledgeReindex: (projectId: number) =>
        `${V1}/projects/${projectId}/knowledge/reindex`,
    projectStages: (projectId: number) => `${V1}/projects/${projectId}/stages`,
    stage: (stageId: number) => `${V1}/stages/${stageId}`,
    projectTasks: (projectId: number) => `${V1}/projects/${projectId}/tasks`,
    taskRephrase: (projectId: number) => `${V1}/projects/${projectId}/tasks/rephrase`,
    projectCalendar: (projectId: number) => `${V1}/projects/${projectId}/calendar`,
    projectCalendarUnscheduled: (projectId: number) =>
        `${V1}/projects/${projectId}/calendar/unscheduled`,
    projectCalendarScenarioPreview: (projectId: number) =>
        `${V1}/projects/${projectId}/calendar/scenarios/preview`,
    projectCalendarScenarioApply: (projectId: number) =>
        `${V1}/projects/${projectId}/calendar/scenarios/apply`,
    projectMilestones: (projectId: number) => `${V1}/projects/${projectId}/milestones`,
    projectMilestone: (projectId: number, milestoneId: number) =>
        `${V1}/projects/${projectId}/milestones/${milestoneId}`,
    projectTaskDependencies: (projectId: number) =>
        `${V1}/projects/${projectId}/task-dependencies`,
    projectTaskDependency: (projectId: number, dependencyId: number) =>
        `${V1}/projects/${projectId}/task-dependencies/${dependencyId}`,
    task: (taskId: number) => `${V1}/tasks/${taskId}`,
    taskBaseline: (taskId: number) => `${V1}/tasks/${taskId}/baseline`,
    taskMove: (taskId: number) => `${V1}/tasks/${taskId}/move`,
    taskComments: (taskId: number) => `${V1}/tasks/${taskId}/comments`,
    taskCommentsWithFiles: (taskId: number) => `${V1}/tasks/${taskId}/comments/with-files`,
    taskActivity: (taskId: number) => `${V1}/tasks/${taskId}/activity`,
    taskAttachments: (taskId: number) => `${V1}/tasks/${taskId}/attachments`,
    taskFilesSearch: (taskId: number) => `${V1}/tasks/${taskId}/files/search`,
    taskAttachment: (taskId: number, attachmentId: number) =>
        `${V1}/tasks/${taskId}/attachments/${attachmentId}`,
    taskAttachmentRetry: (taskId: number, attachmentId: number) => `${V1}/tasks/${taskId}/attachments/${attachmentId}/retry`,
    taskDocumentImport: (taskId: number) => `${V1}/tasks/${taskId}/documents/import`,
    taskLinks: (taskId: number) => `${V1}/tasks/${taskId}/links`,
    projectDocuments: (projectId: number) => `${V1}/projects/${projectId}/documents`,
    projectFiles: (projectId: number) => `${V1}/projects/${projectId}/files`,
    projectFilesSearch: (projectId: number) => `${V1}/projects/${projectId}/files/search`,
    projectFile: (projectId: number, fileId: number) => `${V1}/projects/${projectId}/files/${fileId}`,
    projectFileContent: (projectId: number, fileId: number) => `${V1}/projects/${projectId}/files/${fileId}/content`,
    projectFileRetry: (projectId: number, fileId: number) => `${V1}/projects/${projectId}/files/${fileId}/retry`,
    document: (documentId: number) => `${V1}/documents/${documentId}`,
    documentLinks: (documentId: number) => `${V1}/documents/${documentId}/links`,
    links: () => `${V1}/document-links`,
    link: (linkId: number) => `${V1}/document-links/${linkId}`,
    wbs: (projectId: number) => `${V1}/projects/${projectId}/wbs`,
    wbsNodes: (projectId: number) => `${V1}/projects/${projectId}/wbs/nodes`,
    wbsNode: (projectId: number, nodeId: number) => `${V1}/projects/${projectId}/wbs/nodes/${nodeId}`,
    wbsNodeMove: (projectId: number, nodeId: number) =>
        `${V1}/projects/${projectId}/wbs/nodes/${nodeId}/move`,
    wbsTaskAssign: (projectId: number, taskId: number) =>
        `${V1}/projects/${projectId}/wbs/tasks/${taskId}/assign`,
    wbsTaskAssignment: (projectId: number, taskId: number) =>
        `${V1}/projects/${projectId}/wbs/tasks/${taskId}/assignment`,
    wbsTaskPlacement: (projectId: number, taskId: number) =>
        `${V1}/projects/${projectId}/wbs/tasks/${taskId}/placement`,
    wbsSuggestion: (projectId: number) => `${V1}/projects/${projectId}/wbs/suggestion`,
    wbsSuggestionApply: (projectId: number) =>
        `${V1}/projects/${projectId}/wbs/suggestion/apply`,
projectIconImage: (projectId: number, version?: string | null) =>
        `${V1}/projects/${projectId}/icon-image${version ? `?v=${encodeURIComponent(version)}` : ""}`,
projectBoards: (projectId: number) => `${V1}/projects/${projectId}/boards`,
projectBoard: (projectId: number | null, boardId: number) => boardBase(projectId, boardId),
freeBoards: () => `${V1}/boards`,
boardContent: (projectId: number | null, boardId: number) => `${boardBase(projectId, boardId)}/content`,
boardStickers: (projectId: number | null, boardId: number) => `${boardBase(projectId, boardId)}/stickers`,
boardSticker: (projectId: number | null, boardId: number, stickerId: number) =>
        `${boardBase(projectId, boardId)}/stickers/${stickerId}`,
boardStickerPosition: (projectId: number | null, boardId: number, stickerId: number) =>
        `${boardBase(projectId, boardId)}/stickers/${stickerId}/position`,
boardShapes: (projectId: number | null, boardId: number) => `${boardBase(projectId, boardId)}/shapes`,
boardShape: (projectId: number | null, boardId: number, shapeId: number) =>
        `${boardBase(projectId, boardId)}/shapes/${shapeId}`,
boardShapeToSticker: (projectId: number | null, boardId: number, shapeId: number) =>
        `${boardBase(projectId, boardId)}/shapes/${shapeId}/to-sticker`,
boardBatch: (projectId: number | null, boardId: number) => `${boardBase(projectId, boardId)}/batch`,
timConversations: () => `${V1}/tim/conversations`,
timMessages: (conversationId: number) => `${V1}/tim/conversations/${conversationId}/messages`,
timMessageRetry: (conversationId: number, messageId: number) => `${V1}/tim/conversations/${conversationId}/messages/${messageId}/retry`,
timFiles: (conversationId: number) => `${V1}/tim/conversations/${conversationId}/files`,
timActionDecision: (conversationId: number, actionId: string) => `${V1}/tim/conversations/${conversationId}/actions/${actionId}/decision`,
projectTimActionDecision: (projectId: number, actionId: string) => `${V1}/projects/${projectId}/tim/actions/${actionId}/decision`,
agentPending: (actionId: string) => `${V1}/agent/pending/${encodeURIComponent(actionId)}`,
agentPendingDecision: (actionId: string, decision: "confirm" | "reject") =>
        `${V1}/agent/pending/${encodeURIComponent(actionId)}/${decision}`,
projectCalendarWorkload: (projectId: number) =>
        `${V1}/projects/${projectId}/calendar/workload`,
myCalendar: () => `${V1}/me/calendar`,
myCalendarUnscheduled: () => `${V1}/me/calendar/unscheduled`,
myCalendarIcsToken: () => `${V1}/me/calendar/ics-token`,
portfolioCalendar: () => `${V1}/portfolio/calendar`,
calendarHolidays: (year: number) => `${V1}/calendar/holidays?year=${year}`,
taskByKey: (taskKey: string) => `${V1}/tasks/by-key/${encodeURIComponent(taskKey)}`,
projectBaseline: (projectId: number, dryRun = false) =>
        `${V1}/projects/${projectId}/baseline${dryRun ? "?dry_run=true" : ""}`,
users: (query: string, limit = 20) =>
        `${V1}/users?${new URLSearchParams({ query, limit: String(limit) }).toString()}`,
personalTasks: () => `${V1}/tasks`,
myTasks: (role: string, includeClosed: boolean, attention?: string) =>
        `${V1}/my/tasks?${new URLSearchParams({ role, include_closed: String(includeClosed), ...(attention && { attention }) }).toString()}`,
taskStatus: (taskId: number) => `${V1}/tasks/${taskId}/status`,
taskMoveTo: (taskId: number) => `${V1}/tasks/${taskId}/move-to`,
taskMovePreview: (taskId: number, query: string) => `${V1}/tasks/${taskId}/move-to/preview?${query}`
};

/** Ключи кэша TanStack Query: всё, что относится к проекту, инвалидируется вместе. */
export const queryKeys = {
    currentUser: ["auth", "me"] as const,
    apiTokens: ["auth", "me", "tokens"] as const,
    dashboard: ["dashboard"] as const,
    dashboardAnalytics: (projectId: number | null) =>
        ["dashboard", "analytics", projectId ?? "portfolio"] as const,
    projects: ["projects"] as const,
    project: (projectId: number) => ["projects", projectId] as const,
    projectStats: (projectId: number) => ["projects", projectId, "stats"] as const,
    projectDefaults: ["project-defaults"] as const,
    projectDeadlineHistory: (projectId: number) => ["projects", projectId, "deadline-history"] as const,
    projectRisks: (projectId: number) => ["projects", projectId, "risks"] as const,
    projectRiskList: (projectId: number, query: string) => ["projects", projectId, "risks", "list", query] as const,
    projectRisk: (projectId: number, riskId: number) => ["projects", projectId, "risks", "detail", riskId] as const,
    projectRiskSummary: (projectId: number, query = "") => ["projects", projectId, "risks", "summary", query] as const,
    projectRiskTaskCounts: (projectId: number) => ["projects", projectId, "risks", "task-counts"] as const,
    projectMembers: (projectId: number) => ["projects", projectId, "members"] as const,
    projectStickers: (projectId: number) => ["projects", projectId, "board-stickers"] as const,
    projectKnowledgeStatus: (projectId: number) =>
        ["projects", projectId, "knowledge", "status"] as const,
    stages: (projectId: number) => ["projects", projectId, "stages"] as const,
    tasks: (projectId: number, search?: string) =>
        ["projects", projectId, "tasks", search ?? ""] as const,
    calendar: (projectId: number, query: string) =>
        ["projects", projectId, "calendar", query] as const,
    calendarUnscheduled: (projectId: number, query: string) =>
        ["projects", projectId, "calendar", "unscheduled", query] as const,
    milestones: (projectId: number) => ["projects", projectId, "milestones"] as const,
    taskDependencies: (projectId: number) =>
        ["projects", projectId, "task-dependencies"] as const,
    task: (taskId: number) => ["tasks", taskId] as const,
    taskComments: (taskId: number) => ["tasks", taskId, "comments"] as const,
    taskActivity: (taskId: number) => ["tasks", taskId, "activity"] as const,
    taskAttachments: (taskId: number) => ["tasks", taskId, "attachments"] as const,
    taskFilesSearch: (taskId: number, query: string, offset = 0) => ["tasks", taskId, "files-search", query, offset] as const,
    taskLinks: (taskId: number) => ["tasks", taskId, "links"] as const,
    documents: (projectId: number, search?: string) =>
        ["projects", projectId, "documents", search ?? ""] as const,
    projectFiles: (projectId: number) => ["projects", projectId, "files"] as const,
    projectFilesSearch: (projectId: number, query: string, scope: string, taskId: string, offset = 0) => ["projects", projectId, "files", "search", query, scope, taskId, offset] as const,
    projectFileList: (projectId: number, search: string) => ["projects", projectId, "files", "list", search] as const,
    projectFile: (projectId: number, fileId: number, offset: number) => ["projects", projectId, "files", "detail", fileId, offset] as const,
    document: (documentId: number) => ["documents", documentId] as const,
    documentLinks: (documentId: number) => ["documents", documentId, "links"] as const,
    wbs: (projectId: number) => ["projects", projectId, "wbs"] as const,
runtime: ["runtime"] as const,
agentPending: (actionId: string) => ["agent", "pending", actionId] as const,
dashboardScoped: (scope: "mine" | "all") => ["dashboard", "overview", scope] as const,
projectBoards: (projectId: number) => ["projects", projectId, "boards"] as const,
freeBoards: ["boards"] as const,
freeBoardContent: (boardId: number) => ["boards", boardId, "content"] as const,
boardContent: (projectId: number, boardId: number) => ["projects", projectId, "boards", boardId, "content"] as const,
myCalendar: (query: string) => ["me", "calendar", query] as const,
myCalendarUnscheduled: (query: string) => ["me", "calendar", "unscheduled", query] as const,
myCalendarIcsToken: ["me", "calendar", "ics-token"] as const,
portfolioCalendar: (query: string) => ["portfolio", "calendar", query] as const,
calendarHolidays: (year: number) => ["calendar", "holidays", year] as const,
users: (query: string) => ["users", query] as const,
myTasks: ["my-tasks"] as const,
myTaskList: (role: string, includeClosed: boolean, attention?: string) => ["my-tasks", role, includeClosed, ...(attention ? [attention] : [])] as const
};

function boardBase(projectId: number | null, boardId: number): string {
    return projectId === null ? `${V1}/boards/${boardId}` : `${V1}/projects/${projectId}/boards/${boardId}`;
}
