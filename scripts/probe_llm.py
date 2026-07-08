"""Probe an OpenAI-compatible endpoint and dump raw HTTP details.

Useful when a gateway/relay returns opaque 429 / 5xx / 524 messages and you want
to know:

  * whether the key authenticates at all,
  * which models the gateway advertises,
  * how a minimal chat completion behaves,
  * exact response headers (channel routing, quota hints, rate-limit info),
  * the raw response body (before any client-side JSON parsing).

Runs on Python stdlib only — no extra packages required.

Usage (PowerShell):

    VAR_env\python.exe repo\ViClinicalIE\scripts\probe_llm.py `
        --base-url https://your.gateway/v1 `
        --api-key  sk-... `
        --model    gpt-4o-mini

Env-var fallbacks: OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL.

Exit codes:
    0  chat probe returned 2xx
    1  chat probe returned non-2xx
    2  bad CLI / missing key
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _open(
    url: str,
    *,
    api_key: str,
    method: str = "GET",
    body: Optional[bytes] = None,
    accept: str = "application/json",
    timeout: int = 60,
    extra_headers: Optional[Dict[str, str]] = None,
    stream: bool = False,
) -> Tuple[int, Dict[str, str], str]:
    """Send one HTTP request; return (status, headers, raw_body_text).

    Never raises for HTTP errors — those are captured and returned like a 2xx
    so the caller can inspect them directly.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": accept,
        "User-Agent": "probe-llm/1.0 (+stdlib)",
    }
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            hdrs = {k: v for k, v in resp.headers.items()}
            if stream:
                # Read the stream chunk-by-chunk with a soft cap so we don't hang
                # forever on a broken SSE. 64 KiB / 20 s is plenty for a probe.
                buf: list[str] = []
                deadline = time.monotonic() + 20.0
                total = 0
                for raw_line in resp:
                    piece = raw_line.decode("utf-8", errors="replace")
                    buf.append(piece)
                    total += len(piece)
                    if total > 64 * 1024 or time.monotonic() > deadline:
                        buf.append("\n... [truncated at 64KiB or 20s] ...\n")
                        break
                raw = "".join(buf)
            else:
                raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, hdrs, raw
    except urllib.error.HTTPError as exc:
        hdrs = {}
        try:
            hdrs = {k: v for k, v in exc.headers.items()}
        except Exception:
            pass
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return exc.code, hdrs, raw
    except urllib.error.URLError as exc:
        return 0, {}, f"URL error: {exc.reason!r} (elapsed={time.monotonic() - started:.2f}s)"


def _print_section(title: str) -> None:
    bar = "=" * max(20, min(80, len(title) + 8))
    print(bar)
    print(f"  {title}")
    print(bar)


def _print_headers(hdrs: Dict[str, str]) -> None:
    if not hdrs:
        print("(no headers)")
        return
    # Highlight gateway-relevant headers first if present.
    priority = [
        "x-request-id",
        "x-ratelimit-limit-requests",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-reset-requests",
        "x-ratelimit-limit-tokens",
        "x-ratelimit-remaining-tokens",
        "x-ratelimit-reset-tokens",
        "retry-after",
        "cf-ray",
        "cf-cache-status",
        "server",
        "x-one-api-channel",
        "x-oneapi-channel",
        "x-oneapi-model",
        "x-newapi-channel",
        "x-upstream",
        "x-request-model",
        "x-billed-model",
        "openai-processing-ms",
        "openai-organization",
    ]
    seen = set()
    for name in priority:
        for key, val in hdrs.items():
            if key.lower() == name and key not in seen:
                print(f"  {key}: {val}")
                seen.add(key)
    for key, val in hdrs.items():
        if key in seen:
            continue
        print(f"  {key}: {val}")


def _print_body(raw: str, *, limit: int = 4000) -> None:
    if not raw:
        print("(empty body)")
        return
    # Try to pretty-print JSON.
    try:
        parsed = json.loads(raw)
        pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
        if len(pretty) > limit:
            pretty = pretty[:limit] + f"\n... [truncated at {limit} chars] ..."
        print(pretty)
        return
    except json.JSONDecodeError:
        pass
    text = raw if len(raw) <= limit else raw[:limit] + f"\n... [truncated at {limit} chars] ..."
    print(text)


# --------------------------------------------------------------------------- #
# Probes                                                                      #
# --------------------------------------------------------------------------- #


def probe_models(base_url: str, api_key: str, timeout: int) -> None:
    _print_section("GET /models")
    url = base_url.rstrip("/") + "/models"
    started = time.monotonic()
    status, hdrs, raw = _open(url, api_key=api_key, method="GET", timeout=timeout)
    elapsed = time.monotonic() - started
    print(f"URL     : {url}")
    print(f"Status  : {status}")
    print(f"Elapsed : {elapsed:.2f}s")
    print("Headers :")
    _print_headers(hdrs)
    print("Body    :")
    # For /models, if the response is a big list, summarize.
    if 200 <= status < 300:
        try:
            data = json.loads(raw)
            items = data.get("data") if isinstance(data, dict) else None
            if isinstance(items, list):
                ids = [it.get("id") for it in items if isinstance(it, dict) and it.get("id")]
                print(f"  models advertised ({len(ids)}):")
                for mid in ids[:40]:
                    print(f"    - {mid}")
                if len(ids) > 40:
                    print(f"    ... [+{len(ids) - 40} more]")
                return
        except json.JSONDecodeError:
            pass
    _print_body(raw)


def probe_chat(
    base_url: str,
    api_key: str,
    model: str,
    *,
    timeout: int,
    stream: bool,
    max_tokens: Optional[int],
) -> int:
    label = "POST /chat/completions" + (" (stream)" if stream else "")
    _print_section(label)
    url = base_url.rstrip("/") + "/chat/completions"

    payload: Dict[str, Any] = {
        "model": model,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": "You are a diagnostic echo."},
            {"role": "user", "content": "Reply with exactly: pong"},
        ],
        "stream": bool(stream),
    }
    if max_tokens is not None and max_tokens > 0:
        payload["max_tokens"] = int(max_tokens)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    accept = "text/event-stream" if stream else "application/json"

    print(f"URL     : {url}")
    print(f"Model   : {model}")
    print("Payload :")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    started = time.monotonic()
    status, hdrs, raw = _open(
        url,
        api_key=api_key,
        method="POST",
        body=body,
        accept=accept,
        timeout=timeout,
        stream=stream,
    )
    elapsed = time.monotonic() - started
    print(f"Status  : {status}")
    print(f"Elapsed : {elapsed:.2f}s")
    print("Headers :")
    _print_headers(hdrs)
    print("Body    :")
    _print_body(raw)

    if 200 <= status < 300:
        _interpret_ok(raw, stream=stream)
        return 0

    _interpret_error(status, raw)
    return 1


def _interpret_ok(raw: str, *, stream: bool) -> None:
    print()
    print("Interpretation:")
    if stream:
        # Count 'data:' lines and try to extract any content pieces.
        events = 0
        text_pieces = []
        for line in raw.splitlines():
            if line.startswith("data:"):
                events += 1
                payload = line[5:].lstrip()
                if payload == "[DONE]":
                    continue
                try:
                    evt = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = evt.get("choices") if isinstance(evt, dict) else None
                if not choices:
                    continue
                c0 = choices[0] if isinstance(choices, list) else None
                if not isinstance(c0, dict):
                    continue
                delta = c0.get("delta") if isinstance(c0.get("delta"), dict) else {}
                for k in ("content", "text", "reasoning_content"):
                    v = delta.get(k)
                    if isinstance(v, str) and v:
                        text_pieces.append((k, v))
                        break
        print(f"  SSE data events : {events}")
        print(f"  text pieces     : {len(text_pieces)}")
        if text_pieces:
            key0, _ = text_pieces[0]
            joined = "".join(p for _, p in text_pieces)
            print(f"  first delta key : {key0}")
            print(f"  joined content  : {joined[:200]!r}")
        else:
            print("  WARNING: no `delta.content` / `delta.text` seen — provider uses a non-standard SSE schema")
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print("  body is not JSON")
            return
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            print("  no `choices` field in body")
            return
        c0 = choices[0] if isinstance(choices, list) else None
        if not isinstance(c0, dict):
            print("  choices[0] not an object")
            return
        msg = c0.get("message") if isinstance(c0.get("message"), dict) else {}
        content = msg.get("content")
        if isinstance(content, str):
            print(f"  content         : {content[:200]!r}")
        else:
            print(f"  message.content type: {type(content).__name__}  value={content!r}")
        usage = data.get("usage") if isinstance(data, dict) else None
        if usage:
            print(f"  usage           : {usage}")


def _interpret_error(status: int, raw: str) -> None:
    print()
    print("Interpretation:")
    hint = {
        401: "Auth failed — the API key is invalid, revoked, or scoped to a different service.",
        403: "Forbidden — the key is valid but not allowed to call this model/endpoint (project/org scoping).",
        404: "Endpoint not found — check --base-url (missing /v1?) or the model id.",
        408: "Request timeout at the origin — retry / lower prompt size.",
        413: "Payload too large — split the note.",
        422: "Unprocessable — parameter rejected by the gateway (e.g. `max_tokens` field, unsupported role).",
        429: "Rate-limited by the gateway. Read the body: 'upstream_error' means the gateway's own channel is overloaded or out of quota, NOT your key.",
        500: "Origin/gateway internal error.",
        502: "Bad Gateway between the gateway and its upstream provider.",
        503: "Origin/upstream unavailable.",
        504: "Origin didn't finish in time.",
        520: "Cloudflare: origin returned an unknown response.",
        521: "Cloudflare: origin is down.",
        522: "Cloudflare: origin didn't accept the connection (timeout).",
        523: "Cloudflare: origin unreachable.",
        524: "Cloudflare: origin didn't send a complete response within 120s. Use streaming and cap max_tokens.",
        525: "Cloudflare: TLS handshake with origin failed.",
        526: "Cloudflare: invalid SSL cert on origin.",
        530: "Cloudflare returns 530 alongside a 1xxx error inside its own body.",
    }.get(status)
    if hint:
        print(f"  {hint}")
    else:
        print(f"  Unusual status {status} — inspect body/headers above.")

    # Sniff for a channel/upstream marker in the body.
    lowered = raw.lower()
    for marker in ("upstream_error", "upstream", "channel", "quota", "insufficient", "no available channel"):
        if marker in lowered:
            print(f"  Body mentions '{marker}' — this is a gateway routing issue, not a key issue.")
            break


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe an OpenAI-compatible endpoint and dump raw HTTP details.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        help="OpenAI-compatible base URL (default: env OPENAI_BASE_URL or https://api.openai.com/v1).",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("OPENAI_API_KEY"),
        help="API key (default: env OPENAI_API_KEY).",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        help="Model name to probe (default: env OPENAI_MODEL or gpt-4o-mini).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds (default: 60).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=16,
        help="max_tokens for the chat probe (default: 16). Pass 0 to omit.",
    )
    parser.add_argument(
        "--no-models",
        action="store_true",
        help="Skip the GET /models probe.",
    )
    parser.add_argument(
        "--no-chat",
        action="store_true",
        help="Skip the POST /chat/completions probe.",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Also run a streaming chat probe (in addition to the non-streaming one).",
    )
    parser.add_argument(
        "--only-stream",
        action="store_true",
        help="Only run the streaming chat probe (skip the non-streaming one).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if not args.api_key:
        print("ERROR: no API key. Pass --api-key or set OPENAI_API_KEY.", file=sys.stderr)
        return 2

    _print_section("Config")
    print(f"  base_url : {args.base_url}")
    print(f"  model    : {args.model}")
    print(f"  timeout  : {args.timeout}s")
    key = args.api_key
    if len(key) > 10:
        masked = key[:4] + "…" + key[-4:] + f" (len={len(key)})"
    else:
        masked = f"(len={len(key)})"
    print(f"  api_key  : {masked}")

    max_tokens = args.max_tokens if args.max_tokens and args.max_tokens > 0 else None

    exit_code = 0

    if not args.no_models:
        try:
            probe_models(args.base_url, args.api_key, args.timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"models probe crashed: {exc!r}")

    if args.no_chat:
        return exit_code

    if not args.only_stream:
        try:
            rc = probe_chat(
                args.base_url,
                args.api_key,
                args.model,
                timeout=args.timeout,
                stream=False,
                max_tokens=max_tokens,
            )
            exit_code = exit_code or rc
        except Exception as exc:  # noqa: BLE001
            print(f"non-stream chat probe crashed: {exc!r}")
            exit_code = exit_code or 1

    if args.stream or args.only_stream:
        try:
            rc = probe_chat(
                args.base_url,
                args.api_key,
                args.model,
                timeout=args.timeout,
                stream=True,
                max_tokens=max_tokens,
            )
            exit_code = exit_code or rc
        except Exception as exc:  # noqa: BLE001
            print(f"stream chat probe crashed: {exc!r}")
            exit_code = exit_code or 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
