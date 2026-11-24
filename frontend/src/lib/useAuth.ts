import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, authEndpoints, queryKeys } from "@/lib/api";
import type { LoginPayload, RegisterPayload, User } from "@/lib/types";

/**
 * Текущий пользователь. 401 — это не ошибка, а «не вошёл», поэтому запрос
 * не повторяется и отдаёт `null`.
 */
export function useCurrentUser() {
    return useQuery({
        queryKey: queryKeys.currentUser,
        queryFn: async () => {
            try {
                return await api.get<User>(authEndpoints.me());
            } catch (error) {
                if (error instanceof ApiError && error.status === 401) {
                    return null;
                }
                throw error;
            }
        },
        retry: false,
        staleTime: 60_000,
    });
}

export function useLogin() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (payload: LoginPayload) => api.post<User>(authEndpoints.login(), payload),
        onSuccess: async (user) => {
            await queryClient.cancelQueries();
            queryClient.clear();
            queryClient.setQueryData(queryKeys.currentUser, user);
        },
    });
}

export function useRegister() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (payload: RegisterPayload) =>
            api.post<User>(authEndpoints.register(), payload),
        onSuccess: async (user) => {
            await queryClient.cancelQueries();
            queryClient.clear();
            queryClient.setQueryData(queryKeys.currentUser, user);
        },
    });
}

export function useLogout() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: () => api.post<void>(authEndpoints.logout()),
        onSettled: () => {
            // Чистим кэш целиком: в нём лежат проекты и задачи ушедшего пользователя.
            queryClient.clear();
            queryClient.setQueryData(queryKeys.currentUser, null);
        },
    });
}
