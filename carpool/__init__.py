"""carpool — many agent tasks, one ride.

Lower the cost of agent work at a fixed quality target:
  1. exact operations run as code (zero model calls)
  2. independent reasoning tasks share ONE request, bound to stable IDs
  3. compact evidence only when a preflight says it is safe
  4. every answer is validated; failures fall back individually
"""
from .backends import AnthropicBackend, CommandBackend, MockBackend, make_backend
from .batch import build_batch_prompt, build_single_prompt, parse_batch, parse_single
from .compact import normalize, preflight, verify
from .compile import Cascade, Gate, compile_log, load_compiled
from .exact import ContractError, Index
from .ledger import Ledger, regressions, score
from .router import Result, run_carpool, run_compact_individual, run_individual
from .types import Answer, Completion, Source, Task

__version__ = "0.3.0"
__all__ = [
    "AnthropicBackend", "CommandBackend", "MockBackend", "make_backend",
    "build_batch_prompt", "build_single_prompt", "parse_batch", "parse_single",
    "normalize", "preflight", "verify", "ContractError", "Index",
    "Ledger", "regressions", "score", "Result", "run_carpool", "run_compact_individual", "run_individual",
    "Answer", "Completion", "Source", "Task",
    "Cascade", "Gate", "compile_log", "load_compiled",
]
