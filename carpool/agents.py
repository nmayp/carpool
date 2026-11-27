"""Backends that report REAL provider usage.

Agent CLIs (each call pays the full agent harness — the cost carpool removes):
  * ClaudeCodeBackend  `claude -p --output-format json`     (tested)
  * CodexBackend       `codex exec --json -`                 (parser from documented event format)
  * GeminiCLIBackend   `gemini -p ... --output-format json`  (parser from documented format)

Raw model APIs over HTTP, stdlib only:
  * OpenAICompatBackend  /v1/chat/completions — OpenAI, Groq, Together, vLLM, LM Studio, OpenRouter...
  * GeminiAPIBackend     generateContent
  * OllamaBackend        /api/generate (local)

Spec strings for `make_backend` / the CLI:
  claude-code[:model]  claude-lean[:model]  codex[:model]  gemini-cli[:model]
  openai:<model>  gemini:<model>  ollama:<model>
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
import urllib.request
from typing import Optional

from .types import Completion


# ---------------------------------------------------------------- parsers
# Pure functions so they can be unit-tested against recorded payloads.

def parse_claude_code(stdout: str) -> Completion:
    d = json.loads(stdout)
    u = d.get("usage", {})
    read = u.get("cache_read_input_tokens", 0) or 0
    write = u.get("cache_creation_input_tokens", 0) or 0
    return Completion(
        text=d.get("result", "") or "",
        input_tokens=(u.get("input_tokens", 0) or 0) + read + write,
        cached_input_tokens=read,
        output_tokens=u.get("output_tokens", 0) or 0,
        cost_usd=float(d.get("total_cost_usd", 0) or 0),
    )


def parse_codex_events(stdout: str) -> Completion:
    text, inp, cached, out = "", 0, 0, 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "item.completed":
            item = ev.get("item", {})
            if item.get("type") in ("agent_message", "assistant_message"):
                text = item.get("text", text)
        elif ev.get("type") == "turn.completed":
            u = ev.get("usage", {})
            inp += u.get("input_tokens", 0) or 0
            cached += u.get("cached_input_tokens", 0) or 0
            out += u.get("output_tokens", 0) or 0
    return Completion(text=text, input_tokens=inp, cached_input_tokens=cached, output_tokens=out)


def parse_gemini_cli(stdout: str) -> Completion:
    d = json.loads(stdout[stdout.find("{"):])
    inp = cached = out = 0
    for m in d.get("stats", {}).get("models", {}).values():
        t = m.get("tokens", {})
        inp += t.get("prompt", 0) or 0
        cached += t.get("cached", 0) or 0
        out += (t.get("candidates", 0) or 0) + (t.get("thoughts", 0) or 0)
    return Completion(text=d.get("response", "") or "", input_tokens=inp, cached_input_tokens=cached, output_tokens=out)


def parse_openai(d: dict) -> Completion:
    u = d.get("usage") or {}
    details = u.get("prompt_tokens_details") or {}
    return Completion(
        text=d["choices"][0]["message"].get("content") or "",
        input_tokens=u.get("prompt_tokens", 0) or 0,
        cached_input_tokens=details.get("cached_tokens", 0) or 0,
        output_tokens=u.get("completion_tokens", 0) or 0,
    )


def parse_gemini_api(d: dict) -> Completion:
    u = d.get("usageMetadata") or {}
    parts = (d.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return Completion(
        text="".join(p.get("text", "") for p in parts if not p.get("thought")),
        input_tokens=u.get("promptTokenCount", 0) or 0,
        cached_input_tokens=u.get("cachedContentTokenCount", 0) or 0,
        output_tokens=(u.get("candidatesTokenCount", 0) or 0) + (u.get("thoughtsTokenCount", 0) or 0),
    )


def parse_ollama(d: dict) -> Completion:
    return Completion(
        text=d.get("response", "") or "",
        input_tokens=d.get("prompt_eval_count", 0) or 0,
        output_tokens=d.get("eval_count", 0) or 0,
    )


# ---------------------------------------------------------------- CLI agents

class _CLI:
    parse = staticmethod(lambda s: Completion(s))
    timeout = 900

    def argv(self) -> list[str]:
        raise NotImplementedError

    def __call__(self, prompt: str) -> Completion:
        # empty working dir so the agent has nothing to go and read
        with tempfile.TemporaryDirectory(prefix="carpool-") as cwd:
            t0 = time.perf_counter()
            p = subprocess.run(self.argv(), input=prompt, capture_output=True, text=True,
                               timeout=self.timeout, cwd=cwd)
            dt = time.perf_counter() - t0
        if p.returncode != 0:
            raise RuntimeError(f"{self.argv()[0]} exited {p.returncode}: {(p.stderr or p.stdout)[-800:]}")
        c = self.parse(p.stdout)
        c.seconds = dt
        return c


class ClaudeCodeBackend(_CLI):
    parse = staticmethod(parse_claude_code)

    def __init__(self, model: Optional[str] = None, extra: str = ""):
        self.model, self.extra = model, extra

    def argv(self):
        a = ["claude", "-p", "--output-format", "json", "--no-session-persistence", "--strict-mcp-config"]
        if self.model:
            a += ["--model", self.model]
        return a + shlex.split(self.extra)


class CodexBackend(_CLI):
    parse = staticmethod(parse_codex_events)

    def __init__(self, model: Optional[str] = None):
        self.model = model

    def argv(self):
        a = ["codex", "exec", "--json", "--skip-git-repo-check"]
        if self.model:
            a += ["-m", self.model]
        return a + ["-"]


class GeminiCLIBackend(_CLI):
    parse = staticmethod(parse_gemini_cli)

    def __init__(self, model: Optional[str] = None):
        self.model = model

    def argv(self):
        a = ["gemini", "--output-format", "json"]
        if self.model:
            a += ["-m", self.model]
        return a + ["-p", " "]  # prompt arrives on stdin; -p enables headless mode


# ---------------------------------------------------------------- HTTP APIs

def _post(url: str, body: dict, headers: dict, timeout: float = 600) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class _HTTP:
    def __call__(self, prompt: str) -> Completion:
        t0 = time.perf_counter()
        c = self.call(prompt)
        c.seconds = time.perf_counter() - t0
        return c


class OpenAICompatBackend(_HTTP):
    def __init__(self, model: str, base_url: Optional[str] = None, api_key_env: str = "OPENAI_API_KEY"):
        self.model = model
        self.base = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.key = os.environ.get(api_key_env, "")

    def call(self, prompt):
        d = _post(f"{self.base}/chat/completions",
                  {"model": self.model, "messages": [{"role": "user", "content": prompt}]},
                  {"authorization": f"Bearer {self.key}"} if self.key else {})
        return parse_openai(d)


class GeminiAPIBackend(_HTTP):
    def __init__(self, model: str):
        self.model, self.key = model, os.environ.get("GEMINI_API_KEY", "")

    def call(self, prompt):
        d = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                  {"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
                  {"x-goog-api-key": self.key})
        return parse_gemini_api(d)


class OllamaBackend(_HTTP):
    """Local models. `num_ctx` sets the context window: Ollama's default is small
    and silently truncates long prompts, which would break grouped requests."""

    def __init__(self, model: str, host: Optional[str] = None, num_ctx: int = 32768):
        self.model, self.num_ctx = model, num_ctx
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host

    def call(self, prompt):
        return parse_ollama(_post(f"{self.host}/api/generate",
                                  {"model": self.model, "prompt": prompt, "stream": False,
                                   "options": {"num_ctx": self.num_ctx, "temperature": 0}}, {}, timeout=1800))


def make_agent_backend(spec: str):
    name, _, arg = spec.partition(":")
    arg = arg or None
    if name == "claude-code":
        return ClaudeCodeBackend(arg)
    if name == "claude-lean":
        # Same model through Claude Code, but no tools and a one-line system prompt:
        # a strong baseline with a small fixed part per call (~1k tokens instead of ~26k).
        return ClaudeCodeBackend(arg, extra='--tools "" --system-prompt "Answer the user\'s tasks exactly as instructed."')
    if name == "codex":
        return CodexBackend(arg)
    if name == "gemini-cli":
        return GeminiCLIBackend(arg)
    if name == "openai" and arg:
        return OpenAICompatBackend(arg)
    if name == "gemini" and arg:
        return GeminiAPIBackend(arg)
    if name == "ollama" and arg:
        return OllamaBackend(arg)
    return None
