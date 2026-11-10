import { api } from "@/lib/api";

/** Keys owned by the signed-in local account; secrets are returned only at creation. */
export interface ApiKey {
    id: string;
    label: string;
    key_prefix: string;
    owner_user_id: number | null;
    owner_name: string | null;
    /** Историческая метка старого API; интерфейс не разделяет ключи по типу. */
    scopes: string[];
    expires_at: string | null;
    is_active: boolean;
    revoked_at: string | null;
    revoked_by_name: string | null;
    created_by_name: string | null;
    created_at: string;
    updated_at: string;
    last_used_at: string | null;
}

export interface ApiKeyCreated extends ApiKey {
    /** Секрет: приходит один раз, при выдаче. */
    raw_key: string;
}

export interface ApiScope {
    code: string;
    description: string;
}

export interface ApiKeyUpdatePayload {
    label?: string;
    scopes?: string[];
    expires_at?: string | null;
    is_active?: boolean;
}

/** Срок ключа: бессрочный, действует, скоро истекает или истёк. */
export function expiryState(key: Pick<ApiKey, "expires_at">, now: Date = new Date()): { label: string; tone: "ok" | "warn" | "danger" | "muted" } {
    if (!key.expires_at) return { label: "Бессрочный", tone: "muted" };
    const expires = new Date(key.expires_at);
    const date = expires.toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" });
    const daysLeft = Math.ceil((expires.getTime() - now.getTime()) / 86_400_000);
    if (daysLeft < 0) return { label: `Истёк ${date}`, tone: "danger" };
    if (daysLeft <= 14) return { label: `Истекает ${date}`, tone: "warn" };
    return { label: `До ${date}`, tone: "ok" };
}

/** Состояние ключа: отозван, истёк или действует. */
export function keyStatus(key: Pick<ApiKey, "is_active" | "expires_at">, now: Date = new Date()): "active" | "revoked" | "expired" {
    if (!key.is_active) return "revoked";
    if (key.expires_at && new Date(key.expires_at).getTime() <= now.getTime()) return "expired";
    return "active";
}

/** Поле даты даёт день без времени — ключ работает весь последний день. */
export function endOfDayIso(date: string): string | null {
    return date ? new Date(`${date}T23:59:59`).toISOString() : null;
}

/** ISO-срок обратно в значение поля даты (локальный день). */
export function isoToDateInput(value: string | null): string {
    if (!value) return "";
    const date = new Date(value);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export interface PersonalApiKeyCreatePayload {
    label: string;
    scopes: string[];
    expires_at?: string | null;
}

const PERSONAL_BASE = "/api/v1/users/me/api-keys";

export const personalApiKeyEndpoints = {
    list: () => PERSONAL_BASE,
    scopes: () => `${PERSONAL_BASE}/scopes`,
    one: (keyId: string) => `${PERSONAL_BASE}/${encodeURIComponent(keyId)}`,
};

export const personalApiKeyQueryKeys = {
    list: ["personal-api-keys"] as const,
    scopes: ["personal-api-keys", "scopes"] as const,
};

export const fetchPersonalApiKeys = () => api.get<ApiKey[]>(personalApiKeyEndpoints.list());
export const fetchPersonalApiScopes = () => api.get<ApiScope[]>(personalApiKeyEndpoints.scopes());
export const createPersonalApiKey = (payload: PersonalApiKeyCreatePayload) =>
    api.post<ApiKeyCreated>(personalApiKeyEndpoints.list(), payload);
export const updatePersonalApiKey = (keyId: string, payload: ApiKeyUpdatePayload) =>
    api.patch<ApiKey>(personalApiKeyEndpoints.one(keyId), payload);
