import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { DocumentView } from "@/components/documents/DocumentView";
import { EmptyState } from "@/components/ui/States";

export function DocumentDetailPage() {
    const project = useProjectOutlet();
    const { documentRef } = useParams<{ documentRef: string }>();
    const navigate = useNavigate();
    return <div className="flex h-full min-h-0 min-w-0 flex-col">
        <div className="shrink-0 px-3 py-3 sm:px-5"><Link to={`/projects/${project.key}/docs`} className="touch-target inline-flex items-center gap-1.5 rounded text-[13px] text-muted hover:text-secondary focus-visible:outline-2 focus-visible:outline-accent"><ArrowLeft size={14} aria-hidden="true" />Документы</Link></div>
        {documentRef ? <DocumentView documentRef={documentRef} projectKey={project.key} expectedProjectId={project.id} onDeleted={() => navigate(`/projects/${project.key}/docs`)} /> : <EmptyState title="Документ не указан" description="Выберите документ в каталоге проекта." />}
    </div>;
}
