class VocBaseError(Exception):
    pass

class SQLValidationError(VocBaseError):
    def __init__(self, reason: str, sql: str = ""):
        self.reason = reason
        self.sql = sql
        super().__init__(f"SQL validation failed: {reason}")

class LLMError(VocBaseError):
    pass

class DBExecutionError(VocBaseError):
    pass

class ConfigError(VocBaseError):
    pass
