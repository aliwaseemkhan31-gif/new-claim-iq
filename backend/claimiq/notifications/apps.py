from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "claimiq.notifications"
    label = "notifications"

    def ready(self) -> None:
        from claimiq.analysis.signals import analysis_finished
        from claimiq.ingestion.services.completion import register
        from claimiq.knowledge.signals import knowledge_base_status_changed
        from claimiq.notifications.services import handlers

        register(handlers.on_ingestion_finished)
        knowledge_base_status_changed.connect(
            handlers.on_knowledge_base_status_changed, dispatch_uid="notifications.kb_status"
        )
        analysis_finished.connect(
            handlers.on_analysis_finished, dispatch_uid="notifications.analysis_finished"
        )
