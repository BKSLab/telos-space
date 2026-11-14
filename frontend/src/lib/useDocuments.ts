import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, endpoints, queryKeys } from "./api";
import { documentQuery, documentsPollInterval, type DocumentFilters } from "./documents";
import type { DocumentPage } from "./types";

export function useDocuments(filters: DocumentFilters, enabled = true) {
    const query = documentQuery(filters);
    return useQuery({
        queryKey: queryKeys.documentList(query),
        queryFn: () => api.get<DocumentPage>(`${endpoints.documents()}?${query}`),
        enabled,
        retry: false,
        refetchInterval: query => documentsPollInterval(query.state.data?.items, query.state.status === "error"),
    });
}

export function useRefreshDocuments() {
    const client = useQueryClient();
    return () => {
        void client.invalidateQueries({ queryKey: ["documents"] });
        void client.invalidateQueries({ queryKey: ["tasks"] });
        void client.invalidateQueries({ queryKey: ["projects"] });
    };
}
