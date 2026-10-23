"""Errors a user can see when a document or edit is refused."""


class DocumentError(ValueError):
    """The document was refused. The previous document stays in place."""
