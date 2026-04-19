import uuid
import structlog
from contextvars import ContextVar

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")


def new_trace_id() -> str:
    tid = str(uuid.uuid4())
    _trace_id.set(tid)
    return tid


def get_trace_id() -> str:
    return _trace_id.get()


def setup_logging(level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(__import__("logging"), level)
        ),
    )


def get_logger(name: str):
    return structlog.get_logger(name)
