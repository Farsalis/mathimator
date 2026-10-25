"""Errors a user can see when a document or edit is refused."""


class DocumentError(ValueError):
    """The document or edit was refused. The previous document stays in place.

    ``code`` is the refusal code of the ``rejected`` line. ``field`` names the
    failing value: its JSON path from the document root when project.json is
    read, or the edit field name for an edit. It is None when no single field
    is at fault.
    """

    def __init__(self, message: str, *, code: str = "invalid_document", field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
