export const contextApi = {
    map: (projectId: number, offset = 0, category = "", query = "") => {
        const params = new URLSearchParams({ offset: String(offset), limit: "20", exclude_category: "facts" });
        if (category) params.set("category", category);
        if (query) params.set("q", query);
        return `/api/v1/projects/${projectId}/knowledge/map?${params}`;
    },
    status: (projectId: number) => `/api/v1/projects/${projectId}/knowledge/map/status`,
    refresh: (projectId: number) => `/api/v1/projects/${projectId}/knowledge/map/refresh`,
    git: (projectId: number) => `/api/v1/projects/${projectId}/git`,
};

export interface KnowledgeEvidence {
    source_id: string; native_revision: string | number | null; source_hash: string;
    content_hash: string; title: string; quote: string | null; start: number | null; end: number | null;
    document_ref: string | null; task_id: number | null; checked_at: string; coverage: string;
}
export interface KnowledgeClaim {
    id: string; revision: number; kind: string; category: string; text: string | null; state: string;
    acceptance: string; verification: string; source_ids: string[]; evidence: KnowledgeEvidence[];
    conflicts_with: string[]; updated_at: string; reason: string | null;
}
export interface KnowledgeMapStatus {
    project_id: number; generation: number; built_generation: number | null; status: string;
    updated_at: string | null;
    coverage: {
        sources_total: number; sources_current: number; sources_pending: number;
        sources_failed: number; sources_skipped: number; complete: boolean;
        claims_current: number; claims_conflicting: number; claims_stale: number;
        chars_processed: number; chars_total: number; reasons: string[]; method: string;
    };
}
export interface KnowledgeMap extends KnowledgeMapStatus {
    items: KnowledgeClaim[];
    gaps: { code: string; category: string; source_id?: string | null; message: string }[];
    total: number; offset: number; limit: number; has_more: boolean; next_offset: number | null;
}
export interface GitConnection {
    id: number; project_id: number; provider: "github"; repository_id: number;
    owner: string; repo: string; name: string; purpose: string; ref: string;
    enabled: boolean;
    is_private: boolean; share_private_with_project: boolean; has_credential: boolean;
    has_webhook_secret: boolean; revision: number; generation: number; credential_revision: number;
    state: string; observed_sha: string | null; indexed_sha: string | null;
    observed_at: string | null; indexed_at: string | null; checked_at: string | null;
    next_poll_at?: string | null;
    sync_stage_ids?: number[];
    sync_pending?: boolean;
    poll_interval_seconds?: number;
    last_sync_reason?: "poll" | "manual" | "webhook" | "task_stage" | "configuration" | null;
    last_sync_requested_at?: string | null;
    last_webhook_received_at?: string | null;
    last_webhook_event?: string | null;
    last_webhook_processed_at?: string | null;
    last_error_code: string | null;
    coverage: { total_files: number; cataloged_files: number; indexed_files: number; metadata_files: number; metadata_counts: Record<string, number>; failed_files: number; truncated: boolean };
}
export interface GitConnectionPage {
    items: GitConnection[]; total: number | null; offset: number; limit: number; next_offset: number | null;
    incomplete_reasons?: string[];
}
export interface GitSourceHit {
    connection_id: number; repository_id: number; commit_sha: string; path: string;
    source_url: string; title: string; excerpt: string; file_id: number; stale: boolean; partial: boolean;
    symbol_id?: number; qualified_name?: string; kind?: string; line_start?: number; line_end?: number; offset?: number;
}
export interface GitStructureCoverage {
    total_files: number; indexed_files: number; cataloged_files: number; metadata_files: number;
    failed_files: number; symbols: number; relations: number; unsupported_files: number;
    analyzed_files: number; reasons: string[];
}
export interface GitSnapshotInfo {
    connection_id: number; commit_sha: string; complete: boolean; analyzer_version: string | null;
    analysis_state: "absent" | "extracting" | "resolving" | "published"; coverage: GitStructureCoverage;
}
export interface GitSymbol {
    id: number; file_id: number; path: string; qualified_name: string; name: string; kind: string;
    parent_id: number | null; signature: string | null; line_start: number; line_end: number;
    offset: number; end_offset: number;
}
export interface GitGraphEdge {
    id: number; source_id: number; target_id: number | null; kind: string;
    confidence: "declared" | "resolved" | "candidate" | "unresolved";
    label: string; path: string; line_start: number; line_end: number;
}
export interface GitGraph {
    connection_id: number; commit_sha: string; analyzer_version: string | null;
    nodes: GitSymbol[]; edges: GitGraphEdge[]; truncated: boolean; coverage: GitStructureCoverage;
    task_links?: { task_id: number; change_id: number; kind: string; provider_id: string; path: string }[];
}
export interface GitFilePage {
    text: string; source_url: string; path: string; commit_sha: string;
    line_start: number; line_end: number; offset: number; next_offset: number | null;
    total_chars: number; stale: boolean; partial: boolean;
}
export interface GitTreeEntry {
    path: string; sha: string | null; type: "blob" | "tree" | "commit";
    mode: "100644" | "100755" | "040000" | "120000" | "160000";
    size: number | null;
    index_state: "pending" | "ready" | "metadata" | "error";
    index_reason: string | null;
}
export interface GitTreePage {
    connection_id: number; repository_id: number; commit_sha: string;
    items: GitTreeEntry[]; offset: number; limit: number; next_offset: number | null;
    truncated: boolean; stale: boolean; total_files: number;
}
export const GIT_METADATA_REASONS: Record<string, string> = {
    binary_file: "Двоичные файлы", unsupported_encoding: "Кодировка не распознана",
    lfs_pointer: "Указатели Git LFS", submodule: "Подмодули Git",
};
export interface GitTaskLink {
    id: number; task_id: number; change_id: number; connection_id: number; repository_id: number;
    kind: "commit" | "pull_request"; title: string; source_url: string; commit_sha: string;
    merge_sha: string | null; pull_number: number | null; status: string; revision: number;
    checked_at: string | null; evidence_partial: boolean; last_error_code: string | null;
    evidence: GitEvidence[];
}
export interface GitEvidence {
    kind: "check" | "deployment"; provider_type: "check_run" | "commit_status" | "deployment";
    provider_id: string; connection_id: number; repository_id: number; commit_sha: string;
    status: string; conclusion: string | null; environment: string | null;
    source_url: string; observed_at: string; upstream_at: string | null; is_current: boolean | null;
}
export interface GitTaskLinks { project_id: number; task_id: number; items: GitTaskLink[]; partial: boolean }
export const KNOWLEDGE_STATES: Record<string, string> = {
    empty: "Ещё не построена", pending: "Ожидает обновления", updating: "Обновляется",
    ready: "Обновлена", partial: "Неполный охват", failed: "Ошибка обновления",
};
export const GIT_STATES: Record<string, string> = {
    pending: "В очереди", syncing: "Индексируется", ready: "Готов", partial: "Неполный охват",
    error: "Ошибка", disabled: "Отключён", access_lost: "Доступ потерян",
};
