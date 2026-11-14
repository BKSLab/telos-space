import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { UserSummary } from "@/lib/types";
import { UserSelect } from "@/components/users/UserSelect";

export function LocalUserSelect({ label, selected, onChange, multiple = false, disabled = false }: {
    label: string; selected: UserSummary[]; onChange: (users: UserSummary[]) => void; multiple?: boolean; disabled?: boolean;
}) {
    const [search, setSearch] = useState("");
    const users = useQuery({ queryKey: queryKeys.users(search), queryFn: () => api.get<UserSummary[]>(endpoints.users(search)), staleTime: 30_000 });
    const choices = [...new Map([...selected, ...(users.data ?? [])].map(user => [user.id, user])).values()];
    return <UserSelect label={label} users={choices} selected={selected} onChange={onChange}
        multiple={multiple} disabled={disabled} emptyLabel="Не выбраны" clearLabel="Не назначен"
        searchValue={search} onSearchChange={setSearch} loading={users.isPending}
        error={users.error ? "Не удалось загрузить пользователей." : null} />;
}
