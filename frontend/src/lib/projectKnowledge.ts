export const contextApi = {
    map: (projectId: number, offset = 0) => `/api/v1/projects/${projectId}/knowledge/map?offset=${offset}&limit=20`,
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
export interface KnowledgeMap {
    project_id: number; generation: number; built_generation: number | null; status: string;
    updated_at: string | null; items: KnowledgeClaim[];
    gaps: { code: string; category: string; source_id?: string | null; message: string }[];
    coverage: {
        sources_total: number; sources_current: number; sources_pending: number;
        sources_failed: number; sources_skipped: number; complete: boolean;
        claims_current: number; claims_conflicting: number; claims_stale: number;
        chars_processed: number; chars_total: number; reasons: string[]; method: string;
    };
    total: number; offset: number; limit: number; has_more: boolean; next_offset: number | null;
}
export interface GitConnection {
    id: number; project_id: number; provider: "github"; repository_id: number;
    owner: string; repo: string; name: string; purpose: string; ref: string;
    include_paths: string[]; exclude_patterns: string[]; enabled: boolean;
    is_private: boolean; share_private_with_project: boolean; has_credential: boolean;
    has_webhook_secret: boolean; revision: number; generation: number; credential_revision: number;
    state: string; observed_sha: string | null; indexed_sha: string | null;
    observed_at: string | null; indexed_at: string | null; checked_at: string | null;
    last_error_code: string | null;
    coverage: { total_files: number; indexed_files: number; excluded_files: number; failed_files: number; truncated: boolean };
}
export interface GitConnectionPage {
    items: GitConnection[]; total: number; offset: number; limit: number; next_offset: number | null;
}
export interface GitSourceHit {
    connection_id: number; repository_id: number; commit_sha: string; path: string;
    source_url: string; title: string; excerpt: string; file_id: number; stale: boolean; partial: boolean;
}
export interface GitFilePage {
    text: string; source_url: string; path: string; commit_sha: string;
    line_start: number; line_end: number; offset: number; next_offset: number | null;
    total_chars: number; stale: boolean; partial: boolean;
}
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
