"""Model backends. A backend is any callable: prompt -> Completion.

* AnthropicBackend  - official `anthropic` SDK (pip install carpool[anthropic])
* CommandBackend    - any CLI that reads a prompt on stdin: `claude -p`,
                      `codex exec -`, `gemini`, `ollama run <model>` ...
* MockBackend       - offline, deterministic; for tests and demos only
"""
from __future__ import annotations

import re
import shlex
import subprocess
import time
from typing import Callable, Optional

from .types import Completion

Backend = Callable[[str], Completion]


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class AnthropicBackend:
    def __init__(self, model: str = "claude-opus-5-5", effort: Optional[str] = None, max_tokens: int = 16000):
        import anthropic  # optional dependency

        self.client = anthropic.Anthropic()
        self.model, self.effort, self.max_tokens = model, effort, max_tokens

    def __call__(self, prompt: str) -> Completion:
        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
            # server-side refusal fallback; see Anthropic docs
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        t0 = time.perf_counter()
        r = self.client.beta.messages.create(**kwargs)
        dt = time.perf_counter() - t0
        if r.stop_reason == "refusal":
            text = ""
        else:
            text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        u = r.usage
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        return Completion(
            text=text,
            input_tokens=u.input_tokens + cache_read + cache_write,
            cached_input_tokens=cache_read,
            output_tokens=u.output_tokens,
            seconds=dt,
        )


class AnthropicBatchBackend:
    """Message Batches API: requests are processed asynchronously (usually
    within an hour, at most 24 h) at 50% of standard prices. Use it for
    work that is not time-sensitive. `map` submits every prompt of a run as
    one batch; calling it with a single prompt submits a one-request batch."""

    def __init__(self, model: str = "claude-opus-5-5", effort: Optional[str] = None,
                 max_tokens: int = 16000, poll_seconds: float = 30, client=None):
        if client is None:
            import anthropic  # optional dependency

            client = anthropic.Anthropic()
        self.client = client
        self.model, self.effort, self.max_tokens, self.poll = model, effort, max_tokens, poll_seconds

    def _params(self, prompt: str) -> dict:
        p = {"model": self.model, "max_tokens": self.max_tokens,
             "messages": [{"role": "user", "content": prompt}]}
        if self.effort:
            p["output_config"] = {"effort": self.effort}
        return p

    def map(self, prompts: list[str]) -> list[Completion]:
        t0 = time.perf_counter()
        batch = self.client.messages.batches.create(
            requests=[{"custom_id": f"r{i}", "params": self._params(p)} for i, p in enumerate(prompts)]
        )
        while True:
            batch = self.client.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            time.sleep(self.poll)
        dt = time.perf_counter() - t0
        out: dict[str, Completion] = {}
        for r in self.client.messages.batches.results(batch.id):  # any order: key by custom_id
            if r.result.type != "succeeded":
                out[r.custom_id] = Completion(text="", seconds=dt / len(prompts))
                continue
            m = r.result.message
            text = "" if m.stop_reason == "refusal" else "".join(
                b.text for b in m.content if getattr(b, "type", "") == "text")
            u = m.usage
            read = getattr(u, "cache_read_input_tokens", 0) or 0
            write = getattr(u, "cache_creation_input_tokens", 0) or 0
            out[r.custom_id] = Completion(text=text, input_tokens=u.input_tokens + read + write,
                                          cached_input_tokens=read, output_tokens=u.output_tokens,
                                          seconds=dt / len(prompts))
        return [out.get(f"r{i}", Completion(text="")) for i in range(len(prompts))]

    def __call__(self, prompt: str) -> Completion:
        return self.map([prompt])[0]


class CommandBackend:
    """Pipe the prompt into a CLI. Usage is estimated (chars/4) because most
    CLIs do not print token counts; results are flagged `estimated`."""

    def __init__(self, command: str, overhead_tokens: int = 0, timeout: float = 600):
        self.argv = shlex.split(command)
        self.overhead = overhead_tokens
        self.timeout = timeout

    def __call__(self, prompt: str) -> Completion:
        t0 = time.perf_counter()
        p = subprocess.run(self.argv, input=prompt, capture_output=True, text=True, timeout=self.timeout)
        dt = time.perf_counter() - t0
        if p.returncode != 0:
            raise RuntimeError(f"{self.argv[0]} exited {p.returncode}: {p.stderr[-500:]}")
        return Completion(
            text=p.stdout,
            input_tokens=estimate_tokens(prompt) + self.overhead,
            output_tokens=estimate_tokens(p.stdout),
            seconds=dt,
            estimated=True,
        )


_TASK_RE = re.compile(r"### TASK (\S+)\nQuestion: (.*?)\nSources:\n(.*?)(?=\n\n### TASK |\n*\Z)", re.S)
_SRC_RE = re.compile(r"^\[([^\]]+)\] (.*)$", re.M)


class MockBackend:
    """A pretend model that answers correctly iff the gold answer is present in
    the evidence it was given. Each call pays a fixed `overhead` (the agent
    harness's system prompt + tool schemas, ~16k tokens in the paper's Codex
    runs) — which is exactly what batching amortises."""

    def __init__(self, gold: dict[str, str], overhead: int = 16_000):
        self.gold, self.overhead = gold, overhead

    def _answer(self, tid: str, block: str) -> tuple[str, str]:
        srcs = _SRC_RE.findall(block)
        g = self.gold.get(tid, "")
        for sid, text in srcs:
            if g and g.casefold() in text.casefold():
                return g, sid
        sid, text = srcs[0] if srcs else ("?", "")
        words = re.findall(r"[A-Z][a-z]+", text)
        return (words[-1] if words else "unknown"), sid

    def __call__(self, prompt: str) -> Completion:
        import json

        blocks = _TASK_RE.findall(prompt)
        rows = [dict(zip(("answer", "source"), self._answer(tid, srcs)), id=tid) for tid, _q, srcs in blocks]
        if "Return ONLY a JSON array" in prompt:
            out = json.dumps(rows)
        else:
            r = rows[0] if rows else {"answer": "", "source": ""}
            out = json.dumps({"answer": r["answer"], "source": r["source"]})
        return Completion(
            text=out,
            input_tokens=estimate_tokens(prompt) + self.overhead,
            output_tokens=estimate_tokens(out),
            seconds=0.0,
            estimated=True,
        )


def make_backend(spec: str, **kw) -> Backend:
    """anthropic[:model] | anthropic-batch[:model] | claude-code[:model] | codex[:model] | gemini-cli[:model]
    | openai:<model> | gemini:<model> | ollama:<model> | cmd:<shell command>"""
    from .agents import make_agent_backend

    b = make_agent_backend(spec)
    if b is not None:
        return b
    if spec == "anthropic-batch" or spec.startswith("anthropic-batch:"):
        model = spec.split(":", 1)[1] if ":" in spec else "claude-opus-5-5"
        return AnthropicBatchBackend(model=model, **kw)
    if spec == "anthropic" or spec.startswith("anthropic:"):
        model = spec.split(":", 1)[1] if ":" in spec else "claude-opus-5-5"
        return AnthropicBackend(model=model, **kw)
    if spec.startswith("cmd:"):
        return CommandBackend(spec[4:], **kw)
    raise ValueError(f"unknown backend '{spec}'. See `carpool --help` or carpool/agents.py for specs")
