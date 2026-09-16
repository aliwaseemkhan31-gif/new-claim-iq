from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    name = "claimiq.knowledge"
    label = "knowledge"

    def ready(self) -> None:
        # A processed standard form becomes a knowledge base build. Registered
        # here so ingestion does not import the knowledge app.
        from claimiq.ingestion.services.completion import register
        from claimiq.knowledge.services.build import on_source_processed

        register(on_source_processed)
