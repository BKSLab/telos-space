from src.db.chat_ddl import register_chat_ddl
from src.db.document_catalog_ddl import register_document_catalog_ddl
from src.db.file_processing_ddl import register_file_processing_ddl
from src.db.knowledge_outbox import register_outbox_ddl
from src.db.task_attention_ddl import register_attention_ddl

from .agent_conversations import AgentConversation
from .agent_files import AgentFile
from .agent_messages import AgentMessage
from .agent_tool_runs import AgentToolRun
from .analytics_reports import AnalyticsReport
from .api_key_usage import ApiKeyUsage
from .api_keys import ApiKey
from .api_tokens import ApiToken, ApiTokenScope
from .base import Base
from .board_shapes import BoardShape, BoardShapeKind
from .board_stickers import BoardSticker, BoardStickerTaskLink
from .boards import Board
from .calendar_feed_tokens import CalendarFeedToken
from .chat_agent_turns import ChatAgentTurn
from .chat_attachments import ChatAttachment
from .chat_events import ChatEvent
from .chat_message_entities import ChatMessageEntity
from .chat_message_mentions import ChatMessageMention
from .chat_messages import ChatMessage
from .chat_reactions import ChatReaction
from .chat_read_states import ChatReadState
from .document_index_states import DocumentIndexState
from .document_links import DocumentLink
from .document_submissions import DocumentSubmission
from .documents import Document
from .file_comment_submissions import FileCommentSubmission
from .file_processing_jobs import FileProcessingJob
from .file_search_chunks import FileSearchChunk
from .knowledge_attachment_texts import KnowledgeAttachmentText
from .knowledge_index_jobs import (
    KnowledgeEntityType,
    KnowledgeIndexJob,
    KnowledgeIndexOperation,
    KnowledgeIndexStatus,
)
from .knowledge_project_file_texts import KnowledgeProjectFileText
from .knowledge_source_summaries import KnowledgeSourceSummary
from .project_chats import ProjectChat
from .project_deadline_changes import ProjectDeadlineChange
from .project_files import ProjectFile
from .project_members import ProjectMember, ProjectRole
from .project_milestones import ProjectMilestone, ProjectMilestoneStatus
from .project_risks import ProjectRisk
from .project_stages import ProjectStage
from .projects import Project, ProjectStatus
from .task_activity import TaskActivity, TaskActivityEventType
from .task_attachments import TaskAttachment
from .task_comment_attachments import TaskCommentAttachment
from .task_comment_documents import TaskCommentDocument
from .task_comments import TaskComment
from .task_dependencies import TaskDependency, TaskDependencyType
from .task_participants import TaskParticipant, TaskParticipantRole
from .task_reports import TaskReport
from .task_views import TaskView
from .tasks import Task, TaskPriority, TaskRole
from .users import User
from .wbs_nodes import WbsNode

__all__ = [
    "ProjectFile",
    "KnowledgeProjectFileText",
    "ApiKey",
    "ApiKeyUsage",
    "Board",
    "BoardShape",
    "BoardShapeKind",
    "TaskReport",
    "TaskView",
    "ChatAgentTurn",
    "CalendarFeedToken",
    "AgentFile",
    "AgentConversation",
    "AgentMessage",
    "AgentToolRun",
    "AnalyticsReport",
    "ApiToken",
    "ApiTokenScope",
    "Base",
    "FileCommentSubmission",
    "FileProcessingJob",
    "FileSearchChunk",
    "ChatAttachment",
    "ChatEvent",
    "ChatMessageEntity",
    "ChatMessageMention",
    "ChatMessage",
    "ChatReaction",
    "ChatReadState",
    "ProjectChat",
    "Document",
    "DocumentIndexState",
    "DocumentSubmission",
    "DocumentLink",
    "KnowledgeEntityType",
    "KnowledgeAttachmentText",
    "KnowledgeSourceSummary",
    "KnowledgeIndexJob",
    "KnowledgeIndexOperation",
    "KnowledgeIndexStatus",
    "Project",
    "ProjectDeadlineChange",
    "ProjectMember",
    "ProjectMilestone",
    "ProjectMilestoneStatus",
    "ProjectRole",
    "ProjectRisk",
    "ProjectStage",
    "BoardSticker",
    "BoardStickerTaskLink",
    "ProjectStatus",
    "Task",
    "TaskActivity",
    "TaskActivityEventType",
    "TaskAttachment",
    "TaskComment",
    "TaskCommentAttachment",
    "TaskCommentDocument",
    "TaskDependency",
    "TaskDependencyType",
    "TaskParticipant",
    "TaskParticipantRole",
    "TaskPriority",
    "TaskRole",
    "User",
    "WbsNode",
]


register_outbox_ddl(Base.metadata)
register_chat_ddl(Base.metadata)


register_attention_ddl(Base.metadata)
register_file_processing_ddl(Base.metadata)
register_document_catalog_ddl(Base.metadata)
