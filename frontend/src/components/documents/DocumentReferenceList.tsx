import { useQueries } from "@tanstack/react-query";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { DocumentListItem } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { DocumentList } from "./DocumentList";

/** A comment keeps document references; current access and metadata are read anew. */
export function DocumentReferenceList({ documentRefs }: { documentRefs: string[] }) {
    const queries = useQueries({ queries: [...new Set(documentRefs)].map(ref => ({ queryKey: queryKeys.document(ref), queryFn: () => api.get<DocumentListItem>(endpoints.document(ref)), retry: false, staleTime: 0, refetchOnMount: "always" as const })) });
    const documents = queries.flatMap(query => query.data && query.isFetchedAfterMount && !query.error ? [query.data] : []);
    return <div className="flex min-w-0 flex-col gap-2">
        {queries.some(query => query.isPending || !query.isFetchedAfterMount) && <Skeleton className="h-14 w-full" />}
        {queries.some(query => query.error) && <ErrorMessage title="Не удалось открыть документы комментария" message="Документ недоступен или доступ изменился." action={<Button onClick={() => queries.filter(query => query.error).forEach(query => void query.refetch())}>Повторить загрузку документов</Button>} />}
        {documents.length > 0 && <DocumentList documents={documents} />}
    </div>;
}
