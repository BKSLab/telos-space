from src.db.chat_ddl import register_chat_ddl
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
from .calendar_feed_tokens import CalendarFeedToken
from .chat_agent_turns import ChatAgentTurn
from .chat_attachments import ChatAttachment
from .chat_events import ChatEvent
from .chat_message_entities import ChatMessageEntity
from .chat_message_mentions import ChatMessageMention
from .chat_messages import ChatMessage
from .chat_reactions import ChatReaction
from .chat_read_states import ChatReadState
from .document_links import DocumentLink
from .documents import Document
from .knowledge_attachment_texts import KnowledgeAttachmentText
from .knowledge_index_jobs import (
    KnowledgeEntityType,
    KnowledgeIndexJob,
    KnowledgeIndexOperation,
    KnowledgeIndexStatus,
)
from .knowledge_project_file_texts import KnowledgeProjectFileText
from .knowledge_source_summaries import KnowledgeSourceSummary
from .project_boards import ProjectBoard
from .project_chats import ProjectChat
from .project_deadline_changes import ProjectDeadlineChange
from .project_files import ProjectFile
from .project_members import ProjectMember, ProjectRole
from .project_milestones import ProjectMilestone, ProjectMilestoneStatus
from .project_risks import ProjectRisk
from .project_stages import ProjectStage
from .project_stickers import ProjectSticker, ProjectStickerColor, ProjectStickerTaskLink
from .projects import Project, ProjectStatus
from .task_activity import TaskActivity, TaskActivityEventType
from .task_attachments import TaskAttachment
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
    "ProjectBoard",
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
    "ChatAttachment",
    "ChatEvent",
    "ChatMessageEntity",
    "ChatMessageMention",
    "ChatMessage",
    "ChatReaction",
    "ChatReadState",
    "ProjectChat",
    "Document",
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
    "ProjectSticker",
    "ProjectStickerColor",
    "ProjectStickerTaskLink",
    "ProjectStatus",
    "Task",
    "TaskActivity",
    "TaskActivityEventType",
    "TaskAttachment",
    "TaskComment",
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
