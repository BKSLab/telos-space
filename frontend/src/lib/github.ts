import { api, ApiError } from "./api";
import { contextApi } from "./projectKnowledge";

export const githubPath = "/api/v1/users/me/github";
export const githubAccountKey = ["github", "account"] as const;
export const githubRepositoriesKey = ["github", "repositories"] as const;

export interface GitHubAccountResponse {
    account: { id: number; login: string } | null;
}

export interface GitHubRepository {
    id: number;
    owner: string;
    name: string;
    default_branch: string;
    private: boolean;
}

export interface GitHubRepositoryPage {
    repositories: GitHubRepository[];
    next_page: number | null;
}

export interface RepositoryDraft {
    owner: string;
    repo: string;
    name: string;
    purpose: string;
    ref: string;
    defaultBranch: string;
    private: boolean | null;
    share_private_with_project: boolean;
}

export const repositoryKey = ({ owner, repo }: Pick<RepositoryDraft, "owner" | "repo">) => `${owner}/${repo}`.toLowerCase();

export function repositoryFromUrl(value: string) {
    const match = /^https:\/\/github\.com\/([a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?)\/([a-z0-9_.-]+)\/?$/i.exec(value.trim());
    if (!match) return null;
    const repo = match[2].replace(/\.git$/i, "");
    if (!repo || repo === "." || repo === ".." || repo.length > 100) return null;
    return { owner: match[1], repo };
}

export function createRepositoryDraft(owner: string, repo: string, metadata?: GitHubRepository): RepositoryDraft {
    return {
        owner, repo, name: `${owner}/${repo}`, purpose: "", ref: "",
        defaultBranch: metadata?.default_branch ?? "", private: metadata?.private ?? null,
        share_private_with_project: false,
    };
}

export function validRepositoryDrafts(drafts: RepositoryDraft[]) {
    return drafts.every(draft => draft.private !== true || draft.share_private_with_project);
}

export function connectRepository(projectId: number, draft: RepositoryDraft) {
    const { owner, repo, name, purpose, ref, share_private_with_project } = draft;
    return api.post(`${contextApi.git(projectId)}/connections`, {
        owner, repo, name: name.trim() || `${owner}/${repo}`, purpose,
        ref: ref.trim() || null,
        share_private_with_project,
    });
}

export function githubRetryAt(error: unknown, now = Date.now()) {
    if (!(error instanceof ApiError) || (error.status !== 429 && !/rate_limit|rate limit/i.test(error.message))) return null;
    if (/^\d+$/.test(error.retryAfter ?? "")) return now + Number(error.retryAfter) * 1000;
    const date = Date.parse(error.retryAfter ?? "");
    return Number.isFinite(date) ? Math.max(now, date) : now + 60_000;
}
