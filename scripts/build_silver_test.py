"""Build a silver test set for ViClinicalIE offline evaluation.

Iterates over `input/*.txt`, sends each raw note to an OpenAI-compatible
chat-completions endpoint, and writes one silver `{file_id}.json` per file
following the ABOUT.md submission schema:

    [
      {
        "text": "...",
        "position": [start, end],
        "type": "TRIỆU_CHỨNG|TÊN_XÉT_NGHIỆM|KẾT_QUẢ_XÉT_NGHIỆM|CHẨN_ĐOÁN|THUỐC",
        "assertions": ["isNegated"|"isHistorical"|"isFamily"]*,
        "candidates": ["<icd10 or rxnorm code>", ...]   # only for CHẨN_ĐOÁN / THUỐC
      },
      ...
    ]

The script only depends on the Python stdlib so it can run in any env.

Usage (PowerShell):

    python scripts\build_silver_test.py \
        --base-url https://api.openai.com/v1 \
        --api-key sk-... \
        --model gpt-4o-mini

Falls back to env vars OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL when
flags are omitted.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

VALID_TYPES = {
    "TRIỆU_CHỨNG",
    "TÊN_XÉT_NGHIỆM",
    "KẾT_QUẢ_XÉT_NGHIỆM",
    "CHẨN_ĐOÁN",
    "THUỐC",
}
VALID_ASSERTIONS = ["isNegated", "isFamily", "isHistorical"]
MAPPING_TYPES = {"CHẨN_ĐOÁN", "THUỐC"}


SYSTEM_PROMPT = (
    "Bạn là chuyên gia trích xuất thông tin y khoa tiếng Việt. "
    "Cho một đoạn văn bản lâm sàng, bạn phải trả về danh sách các khái niệm y tế "
    "chính xác theo schema JSON được yêu cầu. Chỉ trả về JSON hợp lệ, không kèm giải thích, "
    "không dùng markdown, không dùng code fence."
)


USER_INSTRUCTIONS = """\
Nhiệm vụ: Trích xuất TẤT CẢ các khái niệm y tế xuất hiện trong đoạn văn bản dưới đây.

Với mỗi khái niệm, xuất ra một object với các trường:
- "text": chuỗi con XUẤT HIỆN NGUYÊN VĂN trong input (giữ nguyên chữ hoa/thường, dấu, khoảng trắng).
- "position": [start, end] là chỉ số ký tự nửa mở trong input (0-indexed, end exclusive), sao cho input[start:end] == text.
- "type": đúng 1 trong 5 nhãn sau (viết chính xác, có dấu, có gạch dưới):
    * "TRIỆU_CHỨNG"      : triệu chứng bệnh nhân mắc phải.
    * "TÊN_XÉT_NGHIỆM"   : tên xét nghiệm (VD: "WBC", "công thức máu", "x-quang ngực").
    * "KẾT_QUẢ_XÉT_NGHIỆM": kết quả xét nghiệm (giá trị và đơn vị nếu có, VD: "14,43", "98.3").
    * "CHẨN_ĐOÁN"        : tên chẩn đoán/bệnh (VD: "bệnh trào ngược dạ dày - thực quản").
    * "THUỐC"            : tên thuốc kèm liều/đường dùng nếu có (VD: "aspirin 325mg x 1").
- "assertions": mảng con của ["isNegated","isFamily","isHistorical"], CHỈ áp dụng cho TRIỆU_CHỨNG, CHẨN_ĐOÁN, THUỐC.
    * "isNegated"   : khái niệm bị phủ định ("không sốt", "chưa ghi nhận", "loại trừ", "không có").
    * "isFamily"    : liên quan đến người nhà/họ hàng ("bố bệnh nhân bị...", "mẹ có tiền sử...").
    * "isHistorical": tiền sử của chính bệnh nhân, thuốc trước nhập viện, bệnh trước đây.
    * Với TÊN_XÉT_NGHIỆM và KẾT_QUẢ_XÉT_NGHIỆM luôn để "assertions": [].
- "candidates": CHỈ với CHẨN_ĐOÁN và THUỐC, là mảng mã chuẩn:
    * CHẨN_ĐOÁN -> mã ICD-10 (VD: ["K21.0","K21.9"]).
    * THUỐC     -> mã RxNorm (VD: ["308135"]).
    * Nếu không chắc chắn, trả về mảng rỗng []. KHÔNG bịa mã.
    * Với TRIỆU_CHỨNG / TÊN_XÉT_NGHIỆM / KẾT_QUẢ_XÉT_NGHIỆM: BỎ trường candidates.

Quy tắc quan trọng:
1. "text" PHẢI khớp NGUYÊN VĂN với input[start:end] (kể cả khoảng trắng thừa nếu có trong input).
2. Không bỏ sót triệu chứng, thuốc, chẩn đoán, xét nghiệm nào.
3. Không tách các cụm thuốc "tên + liều + đường dùng" ra thành nhiều span; giữ nguyên như một khái niệm THUỐC.
4. Tách riêng TÊN_XÉT_NGHIỆM và KẾT_QUẢ_XÉT_NGHIỆM khi cả hai xuất hiện.
5. Trả về DUY NHẤT một mảng JSON hợp lệ, không có văn bản đi kèm.

Định dạng đầu ra bắt buộc là 1 JSON array, ví dụ:
[
  {"text": "...", "position": [s,e], "type": "TRIỆU_CHỨNG", "assertions": []},
  {"text": "...", "position": [s,e], "type": "THUỐC", "assertions": ["isHistorical"], "candidates": ["308135"]}
]

Văn bản input (giữ NGUYÊN VĂN, KHÔNG chỉnh sửa):
---BEGIN INPUT---
{note}
---END INPUT---
"""


# --------------------------------------------------------------------------- #
# HTTP layer                                                                  #
# --------------------------------------------------------------------------- #


class LLMError(RuntimeError):
    """Raised when the chat-completions call cannot produce usable content."""


# HTTP codes worth retrying. 5xx origin + Cloudflare 52x edge errors (524 == origin
# read-timeout at the proxy). 408/425 are transient client-side edge cases.
_RETRY_STATUS = frozenset(
    {408, 409, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530}
)


def chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    *,
    temperature: float = 0.0,
    timeout: int = 180,
    max_retries: int = 4,
    max_tokens: Optional[int] = 2000,
    stream: bool = True,
    extra_headers: Optional[Dict[str, str]] = None,
) -> str:
    """Call an OpenAI-compatible /chat/completions endpoint and return the message content.

    Uses SSE streaming by default so bytes keep flowing through proxies (e.g. Cloudflare)
    and long generations don't trigger a 524 origin-read-timeout at the edge.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": bool(stream),
    }
    if max_tokens is not None and max_tokens > 0:
        payload["max_tokens"] = int(max_tokens)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/event-stream" if stream else "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    last_err: Optional[Exception] = None
    stream_effective = bool(stream)
    for attempt in range(1, max_retries + 1):
        # Rebuild body+headers each attempt so we can flip streaming off as a fallback.
        payload["stream"] = stream_effective
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Accept"] = "text/event-stream" if stream_effective else "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if stream_effective:
                    content, diag = _consume_sse_content(resp)
                    if content:
                        return content
                    # Stream returned nothing usable — fall back to non-streaming.
                    last_err = LLMError(
                        f"empty stream (events={diag['events']}, "
                        f"unknown_keys={sorted(diag['unknown_keys'])[:6]}, "
                        f"sample={diag['sample'][:200]!r})"
                    )
                    if attempt < max_retries and stream_effective:
                        stream_effective = False
                        continue
                    raise last_err
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            last_err = LLMError(f"HTTP {exc.code}: {err_body[:500]}")
            if exc.code in _RETRY_STATUS and attempt < max_retries:
                retry_after = _retry_after_seconds(exc.headers)
                base = retry_after if retry_after is not None else min(2 ** attempt, 20)
                # Add jitter so parallel workers don't line up their retries.
                time.sleep(base + random.uniform(0.0, 1.5))
                continue
            raise last_err from exc
        except urllib.error.URLError as exc:
            last_err = LLMError(f"URL error: {exc.reason!r}")
            if attempt < max_retries:
                time.sleep(min(2 ** attempt, 20) + random.uniform(0.0, 1.5))
                continue
            raise last_err from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"non-JSON response body: {raw[:500]}") from exc

        content = _extract_message_content(data)
        if content:
            return content
        raise LLMError(f"unexpected response shape: {raw[:500]}")

    raise last_err or LLMError("chat completion failed for unknown reason")


def _retry_after_seconds(headers: Any) -> Optional[float]:
    """Parse a `Retry-After` header (either delta-seconds or an HTTP-date)."""
    if headers is None:
        return None
    try:
        raw = headers.get("Retry-After")
    except Exception:
        raw = None
    if not raw:
        return None
    raw = str(raw).strip()
    # 1) integer seconds
    if raw.isdigit():
        try:
            return max(0.0, float(raw))
        except ValueError:
            return None
    # 2) HTTP-date
    try:
        target = parsedate_to_datetime(raw)
        if target is None:
            return None
        delta = target.timestamp() - time.time()
        return max(0.0, delta)
    except (TypeError, ValueError):
        return None


def _extract_message_content(data: Any) -> str:
    """Pull the assistant text from a non-streaming chat/completions response.

    Handles minor provider variants: standard OpenAI (`choices[].message.content`),
    `choices[].text` (older completions style), and list-typed `content` used by some
    Anthropic/OpenAI-compat gateways.
    """
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        c0 = choices[0]
        if isinstance(c0, dict):
            msg = c0.get("message")
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and content:
                    return content
                if isinstance(content, list):
                    parts: List[str] = []
                    for part in content:
                        if isinstance(part, dict):
                            t = part.get("text") or part.get("content")
                            if isinstance(t, str):
                                parts.append(t)
                        elif isinstance(part, str):
                            parts.append(part)
                    if parts:
                        return "".join(parts)
            text = c0.get("text")
            if isinstance(text, str) and text:
                return text
    # Some gateways return {"output_text": "..."} or {"content": "..."} at top level.
    for key in ("output_text", "content", "text"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


# Keys some non-OpenAI-perfect gateways use inside `delta` / event payload.
# Do NOT include reasoning-only fields (for example `reasoning_content`). Some
# reasoning models stream their chain-of-thought separately before the final
# assistant answer. Treating that as answer text makes downstream JSON parsing
# fail with prose such as "We need to extract..." instead of the final JSON.
_DELTA_TEXT_KEYS = ("content", "text", "output_text", "response")
_REASONING_TEXT_KEYS = ("reasoning_content", "reasoning", "thoughts")


def _consume_sse_content(resp) -> Tuple[str, Dict[str, Any]]:
    """Read an OpenAI-style SSE stream. Return (joined_text, diagnostics).

    diagnostics contains:
      - events: total number of data events seen
      - unknown_keys: set of keys observed on delta/message when no text was extracted
      - sample: first ~500 chars of the raw stream (helps debug non-standard providers)
    """
    parts: List[str] = []
    unknown_keys: set[str] = set()
    reasoning_events = 0
    events = 0
    sample_buf: List[str] = []
    sample_left = 500

    def _pluck_text(obj: Any) -> Optional[str]:
        if isinstance(obj, str):
            return obj or None
        if isinstance(obj, dict):
            for key in _DELTA_TEXT_KEYS:
                val = obj.get(key)
                if isinstance(val, str) and val:
                    return val
                if isinstance(val, list):
                    collected: List[str] = []
                    for item in val:
                        if isinstance(item, str) and item:
                            collected.append(item)
                        elif isinstance(item, dict):
                            t = item.get("text") or item.get("content")
                            if isinstance(t, str) and t:
                                collected.append(t)
                    if collected:
                        return "".join(collected)
        return None

    def _has_reasoning(obj: Any) -> bool:
        if isinstance(obj, dict):
            for key in _REASONING_TEXT_KEYS:
                val = obj.get(key)
                if isinstance(val, str) and val:
                    return True
            return any(_has_reasoning(v) for v in obj.values())
        if isinstance(obj, list):
            return any(_has_reasoning(item) for item in obj)
        return False

    for raw_line in resp:
        if not raw_line:
            continue
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if sample_left > 0 and line:
            snippet = line[:sample_left]
            sample_buf.append(snippet)
            sample_left -= len(snippet)
        if not line or line.startswith(":") or line.startswith("event:"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].lstrip()
        if payload == "[DONE]":
            break
        try:
            evt = json.loads(payload)
        except json.JSONDecodeError:
            continue
        events += 1

        # Standard OpenAI chat.completion.chunk: choices[0].delta.{content,...}
        choices = evt.get("choices") if isinstance(evt, dict) else None
        piece: Optional[str] = None
        if isinstance(choices, list) and choices:
            c0 = choices[0]
            if isinstance(c0, dict):
                delta = c0.get("delta")
                piece = _pluck_text(delta) if delta is not None else None
                if not piece:
                    piece = _pluck_text(c0.get("message"))
                if not piece:
                    piece = _pluck_text(c0.get("text"))
                if not piece and _has_reasoning(delta):
                    reasoning_events += 1
                if not piece and isinstance(delta, dict):
                    unknown_keys.update(k for k in delta.keys() if k not in _REASONING_TEXT_KEYS)
        # Fallbacks for non-standard gateways. Skip whole-event fallback when the
        # event contains only reasoning text; otherwise we may append reasoning
        # prose before the final JSON answer.
        if not piece and not _has_reasoning(evt):
            piece = _pluck_text(evt)
        if not piece and isinstance(evt, dict):
            unknown_keys.update(k for k in evt.keys() if k not in ("choices", *_REASONING_TEXT_KEYS))
        if piece:
            parts.append(piece)

    diag: Dict[str, Any] = {
        "events": events,
        "reasoning_events": reasoning_events,
        "unknown_keys": unknown_keys,
        "sample": "\n".join(sample_buf),
    }
    return "".join(parts), diag


# --------------------------------------------------------------------------- #
# Response parsing                                                            #
# --------------------------------------------------------------------------- #


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json_array(content: str) -> List[Dict[str, Any]]:
    """Best-effort extraction of a JSON array from a raw LLM message."""
    if not content:
        raise LLMError("empty content")

    # Try direct parse first.
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            for key in ("entities", "results", "data", "items", "output"):
                if isinstance(parsed.get(key), list):
                    return parsed[key]
    except json.JSONDecodeError:
        pass

    # Try fenced code blocks.
    for match in _FENCE_RE.finditer(content):
        chunk = match.group(1).strip()
        try:
            parsed = json.loads(chunk)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            continue

    # Fall back to the first [...] balanced block.
    start = content.find("[")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for idx in range(start, len(content)):
            ch = content[idx]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    candidate = content[start : idx + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, list):
                            return parsed
                    except json.JSONDecodeError:
                        break
        start = content.find("[", start + 1)

    raise LLMError(f"could not parse JSON array from response: {content[:300]}")


# --------------------------------------------------------------------------- #
# Entity validation & offset repair                                           #
# --------------------------------------------------------------------------- #


def _normalize_for_search(text: str) -> str:
    """Casefold + NFC + collapse whitespace for fuzzy offset recovery."""
    text = unicodedata.normalize("NFC", text)
    text = text.casefold()
    return text


def _find_offset(raw: str, needle: str, hint: Optional[int]) -> Optional[Tuple[int, int]]:
    """Find `needle` in `raw`, preferring hits close to `hint`."""
    if not needle:
        return None

    # 1. Exact match near hint.
    matches = [m.start() for m in re.finditer(re.escape(needle), raw)]
    if matches:
        if hint is None:
            best = matches[0]
        else:
            best = min(matches, key=lambda pos: abs(pos - hint))
        return best, best + len(needle)

    # 2. Casefold + NFC match; recover raw slice by length.
    raw_norm = _normalize_for_search(raw)
    needle_norm = _normalize_for_search(needle)
    if not needle_norm:
        return None
    fuzzy = [m.start() for m in re.finditer(re.escape(needle_norm), raw_norm)]
    if fuzzy:
        if hint is None:
            pos = fuzzy[0]
        else:
            pos = min(fuzzy, key=lambda p: abs(p - hint))
        return pos, pos + len(needle)

    # 3. Whitespace-tolerant regex fallback.
    pattern = re.compile(
        r"\s*".join(re.escape(tok) for tok in needle.split()),
        re.IGNORECASE,
    )
    ws_matches = list(pattern.finditer(raw))
    if ws_matches:
        if hint is None:
            m = ws_matches[0]
        else:
            m = min(ws_matches, key=lambda mm: abs(mm.start() - hint))
        return m.start(), m.end()

    return None


def _clean_assertions(value: Any, entity_type: str) -> List[str]:
    if entity_type in {"TÊN_XÉT_NGHIỆM", "KẾT_QUẢ_XÉT_NGHIỆM"}:
        return []
    if not isinstance(value, list):
        return []
    keep = [a for a in VALID_ASSERTIONS if a in value]
    return keep


def _clean_candidates(value: Any, entity_type: str) -> Optional[List[str]]:
    if entity_type not in MAPPING_TYPES:
        return None
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen = set()
    for item in value:
        if isinstance(item, (str, int, float)):
            code = str(item).strip()
            if code and code not in seen:
                seen.add(code)
                out.append(code)
    return out


def sanitize_entities(
    raw_entities: Iterable[Dict[str, Any]],
    raw_text: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Validate + repair LLM output against the raw note.

    Returns (entities, stats) where stats includes counts of accepted, offset-fixed,
    and dropped entities.
    """
    accepted: List[Dict[str, Any]] = []
    stats = {"input": 0, "accepted": 0, "offset_fixed": 0, "dropped": 0}

    for item in raw_entities:
        stats["input"] += 1
        if not isinstance(item, dict):
            stats["dropped"] += 1
            continue

        entity_type = str(item.get("type", "")).strip()
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            stats["dropped"] += 1
            continue
        if entity_type not in VALID_TYPES:
            stats["dropped"] += 1
            continue

        pos = item.get("position")
        hint_start: Optional[int] = None
        start = end = None
        if (
            isinstance(pos, list)
            and len(pos) == 2
            and all(isinstance(v, int) for v in pos)
        ):
            s, e = pos
            hint_start = s
            if 0 <= s < e <= len(raw_text) and raw_text[s:e] == text:
                start, end = s, e

        if start is None:
            found = _find_offset(raw_text, text, hint_start)
            if not found:
                stats["dropped"] += 1
                continue
            start, end = found
            # Use the raw slice as the canonical text so `raw[start:end] == text` holds.
            text = raw_text[start:end]
            stats["offset_fixed"] += 1

        entity: Dict[str, Any] = {
            "text": text,
            "position": [start, end],
            "type": entity_type,
            "assertions": _clean_assertions(item.get("assertions"), entity_type),
        }
        candidates = _clean_candidates(item.get("candidates"), entity_type)
        if candidates is not None:
            entity["candidates"] = candidates

        accepted.append(entity)
        stats["accepted"] += 1

    # Deterministic ordering by span.
    accepted.sort(key=lambda ent: (ent["position"][0], ent["position"][1], ent["type"]))
    return accepted, stats


# --------------------------------------------------------------------------- #
# Per-file worker                                                             #
# --------------------------------------------------------------------------- #


def process_file(
    txt_path: Path,
    output_dir: Path,
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float,
    timeout: int,
    max_retries: int,
    overwrite: bool,
    max_tokens: Optional[int] = 2000,
    stream: bool = True,
) -> Dict[str, Any]:
    """Generate silver JSON for one input file."""
    file_id = txt_path.stem
    out_path = output_dir / f"{file_id}.json"

    result: Dict[str, Any] = {
        "file_id": file_id,
        "status": "ok",
        "entities": 0,
        "offset_fixed": 0,
        "dropped": 0,
        "error": None,
    }

    if out_path.exists() and not overwrite:
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                result["status"] = "skipped"
                result["entities"] = len(existing)
                return result
        except Exception:
            pass  # fall through and regenerate

    raw_text = txt_path.read_text(encoding="utf-8")
    user_msg = USER_INSTRUCTIONS.replace("{note}", raw_text)

    try:
        content = chat_completion(
            base_url=base_url,
            api_key=api_key,
            model=model,
            system=SYSTEM_PROMPT,
            user=user_msg,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
            max_tokens=max_tokens,
            stream=stream,
        )
        raw_entities = extract_json_array(content)
    except LLMError as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        return result

    entities, stats = sanitize_entities(raw_entities, raw_text)
    out_path.write_text(
        json.dumps(entities, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )

    result["entities"] = stats["accepted"]
    result["offset_fixed"] = stats["offset_fixed"]
    result["dropped"] = stats["dropped"]
    return result


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a silver test set for ViClinicalIE by calling an OpenAI-compatible LLM endpoint.",
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
        help="Model name (default: env OPENAI_MODEL or gpt-4o-mini).",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=ROOT / "input",
        help=f"Input directory containing {{id}}.txt files (default: {ROOT / 'input'}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "silver_test" / "output",
        help=f"Silver output directory (default: {ROOT / 'silver_test' / 'output'}).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Number of parallel requests (default: 2). Lower this if you see 5xx / 524 errors.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N files (0 = all). Useful for smoke tests.",
    )
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated file_ids to process (e.g. '1,2,7'). Empty = all.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature (default: 0.0).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="HTTP timeout in seconds (default: 180).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Max retries per request on 429/5xx/52x/URL errors (default: 5).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=2000,
        help="Max tokens for the model response. Caps generation time to help avoid Cloudflare 524. Set 0 to disable.",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable SSE streaming. Streaming is on by default to avoid 524 proxy-read-timeouts.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate silver JSON even if the target file already exists.",
    )
    return parser.parse_args(argv)


def select_input_files(
    input_dir: Path,
    only: str,
    limit: int,
) -> List[Path]:
    files = sorted(
        input_dir.glob("*.txt"),
        key=lambda p: (int(p.stem) if p.stem.isdigit() else float("inf"), p.stem),
    )
    if only.strip():
        wanted = {tok.strip() for tok in only.split(",") if tok.strip()}
        files = [p for p in files if p.stem in wanted]
    if limit and limit > 0:
        files = files[:limit]
    return files


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    configure_stdout()
    args = parse_args(argv)

    if not args.api_key:
        print(
            "ERROR: no API key provided. Pass --api-key or set OPENAI_API_KEY.",
            file=sys.stderr,
        )
        return 2

    input_dir: Path = args.input_dir
    output_dir: Path = args.output_dir
    if not input_dir.exists():
        print(f"ERROR: input dir not found: {input_dir}", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    files = select_input_files(input_dir, args.only, args.limit)
    if not files:
        print(f"No .txt files matched under {input_dir}.")
        return 0

    print("=" * 70)
    print("Silver test builder")
    print("=" * 70)
    print(f"base_url   : {args.base_url}")
    print(f"model      : {args.model}")
    print(f"input_dir  : {input_dir}")
    print(f"output_dir : {output_dir}")
    print(f"files      : {len(files)}")
    print(f"concurrency: {args.concurrency}")
    print("=" * 70)

    results: List[Dict[str, Any]] = []
    total = len(files)

    max_tokens_arg: Optional[int] = args.max_tokens if args.max_tokens and args.max_tokens > 0 else None
    stream_arg: bool = not args.no_stream

    def _run(path: Path) -> Dict[str, Any]:
        return process_file(
            path,
            output_dir,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            temperature=args.temperature,
            timeout=args.timeout,
            max_retries=args.max_retries,
            overwrite=args.overwrite,
            max_tokens=max_tokens_arg,
            stream=stream_arg,
        )

    concurrency = max(1, args.concurrency)
    if concurrency == 1:
        for idx, path in enumerate(files, 1):
            res = _run(path)
            results.append(res)
            _report(idx, total, res)
    else:
        with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
            future_to_path = {pool.submit(_run, path): path for path in files}
            for idx, future in enumerate(cf.as_completed(future_to_path), 1):
                path = future_to_path[future]
                try:
                    res = future.result()
                except Exception as exc:  # noqa: BLE001
                    res = {
                        "file_id": path.stem,
                        "status": "error",
                        "entities": 0,
                        "offset_fixed": 0,
                        "dropped": 0,
                        "error": repr(exc),
                    }
                results.append(res)
                _report(idx, total, res)

    manifest_path = output_dir.parent / "silver_manifest.json"
    manifest = {
        "base_url": args.base_url,
        "model": args.model,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "files": sorted(results, key=lambda r: (int(r["file_id"]) if r["file_id"].isdigit() else 1 << 30, r["file_id"])),
        "totals": {
            "files": total,
            "ok": sum(1 for r in results if r["status"] == "ok"),
            "skipped": sum(1 for r in results if r["status"] == "skipped"),
            "errors": sum(1 for r in results if r["status"] == "error"),
            "entities": sum(r["entities"] for r in results),
            "offset_fixed": sum(r["offset_fixed"] for r in results),
            "dropped": sum(r["dropped"] for r in results),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 70)
    print(f"Saved silver JSON to: {output_dir}")
    print(f"Manifest           : {manifest_path}")
    print(f"Totals             : {manifest['totals']}")
    return 0 if manifest["totals"]["errors"] == 0 else 1


def _report(idx: int, total: int, res: Dict[str, Any]) -> None:
    tag = res["status"].upper()
    err = f" | {res['error']}" if res.get("error") else ""
    print(
        f"[{idx:>3}/{total}] {res['file_id']:>4}  {tag:<7} "
        f"entities={res['entities']:>3} fixed={res['offset_fixed']:>2} dropped={res['dropped']:>2}{err}"
    )


if __name__ == "__main__":
    sys.exit(main())
