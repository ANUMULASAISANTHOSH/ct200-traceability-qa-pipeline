"""Domain and application exceptions for document operations."""


class DocumentNotFoundError(Exception):
    """Raised when a requested document slug or ID is not found."""
    pass


class VersionNotFoundError(Exception):
    """Raised when a requested document version is not found."""
    pass


class NodeNotFoundError(Exception):
    """Raised when a requested node version ID is not found."""
    pass


class CrossDocumentMismatchError(Exception):
    """Raised when a version or node does not belong to the requested document."""
    pass


class InvalidQueryError(Exception):
    """Raised when query parameters or combination of arguments is invalid."""
    pass


class SelectionNotFoundError(Exception):
    """Raised when a requested selection ID is not found."""
    pass


class ConfigurationError(Exception):
    """Raised when application or provider configuration is missing or invalid."""
    pass


class LLMProviderError(Exception):
    """Raised when an external LLM provider encounters a network or service failure."""
    pass


class LLMValidationError(Exception):
    """Raised when LLM response output fails schema or domain reference validation."""
    pass


class GenerationNotFoundError(Exception):
    """Raised when a requested generation record is not found."""
    pass


