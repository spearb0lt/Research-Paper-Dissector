"""Streaming a completion, token by token.

A lens on a long paper blocks for nearly two minutes. Measured: the credibility
lens on ColPali took 112 seconds to return, during which the screen showed a
spinner and nothing else. That is the worst interaction in the application, and
it is not a speed problem, it is a feedback problem: the same 112 seconds spent
watching the answer being written reads as working, and spent watching a
spinner reads as broken.

This sits beside the provider adapters rather than inside them, for two
reasons. The non streaming path is what the command line, the lens cache and
every eval use, and it should not grow a second code path it does not need. And
streaming support varies: a provider that cannot stream falls back here to
generating normally and yielding the result in one piece, so a caller never has
to ask whether streaming is available.

The final text is identical either way, because the same sanitisation runs on
the assembled string. What the caller gets is the chance to show it sooner.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from ..ops import meter
from .base import Completion, ImagePart, LLMError, Usage, sanitise_output
from .registry import resolve


def _openai_stream(provider, model: str, prompt: str, system: str | None,
                   temperature: float, max_tokens: int,
                   images: Sequence[ImagePart]) -> Iterator[str]:
    from .providers import _compose_system

    client = provider._client()
    content: Any = prompt
    if images:
        import base64

        content = [{"type": "text", "text": prompt}]
        for image in images:
            encoded = base64.b64encode(image.data).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{image.media_type};base64,{encoded}"},
            })

    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _compose_system(system)},
            {"role": "user", "content": content},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    for chunk in stream:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        piece = getattr(getattr(choices[0], "delta", None), "content", None)
        if piece:
            yield piece


def _gemini_stream(provider, model: str, prompt: str, system: str | None,
                   temperature: float, max_tokens: int,
                   images: Sequence[ImagePart]) -> Iterator[str]:
    from google.genai import types

    from .providers import _compose_system, _gemini_contents

    client = provider._client()
    config: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_tokens,
        "system_instruction": _compose_system(system),
    }
    for chunk in client.models.generate_content_stream(
        model=model,
        contents=_gemini_contents(prompt, images),
        config=types.GenerateContentConfig(**config),
    ):
        piece = getattr(chunk, "text", None)
        if piece:
            yield piece


def _anthropic_stream(provider, model: str, prompt: str, system: str | None,
                      temperature: float, max_tokens: int,
                      images: Sequence[ImagePart]) -> Iterator[str]:
    from .providers import _anthropic_content, _compose_system

    client = provider._client()
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=_compose_system(system),
        messages=[{"role": "user", "content": _anthropic_content(prompt, images)}],
    ) as stream:
        for piece in stream.text_stream:
            if piece:
                yield piece


_STREAMERS = {
    "gemini": _gemini_stream,
    "anthropic": _anthropic_stream,
}


def stream(
    prompt: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    system: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    operation: str = "",
    images: Sequence[ImagePart] = (),
) -> Iterator[str]:
    """Yield an answer in pieces, falling back to one piece when it must.

    The pieces are raw. Sanitisation runs on the assembled whole, because a
    typographic dash can arrive split across two chunks and a per chunk
    replacement would miss it.
    """
    selection = resolve(provider, model)
    adapter = selection.provider
    if images and not adapter.supports_vision:
        images = ()

    streamer = _STREAMERS.get(adapter.id)
    if streamer is None and hasattr(adapter, "_client"):
        # Every OpenAI protocol gateway streams the same way, so one adapter
        # check covers fourteen providers.
        streamer = _openai_stream

    pieces: list[str] = []
    try:
        if streamer is None:
            raise NotImplementedError
        for piece in streamer(
            adapter, selection.model, prompt, system, temperature, max_tokens, images
        ):
            pieces.append(piece)
            yield piece
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 - fall back rather than fail
        if pieces:
            # Already part way through. Reporting a failure now would leave the
            # caller with half an answer and no way to know it was truncated.
            raise LLMError(
                f"The answer was cut short: {str(exc)[:200]}",
                provider=adapter.id,
                model=selection.model,
                retryable=True,
            ) from exc
        completion = adapter.generate(
            prompt,
            model=selection.model,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            images=images,
        )
        _record(completion, operation)
        yield completion.text
        return

    text = "".join(pieces)
    # A streaming response rarely reports usage, so it is estimated from
    # characters rather than left at zero, which would make the cost meter
    # silently understate every streamed answer.
    _record(
        Completion(
            text=sanitise_output(text),
            usage=Usage(
                provider=adapter.id,
                model=selection.model,
                input_tokens=len(prompt) // 4,
                output_tokens=len(text) // 4,
            ),
        ),
        operation,
    )


def _record(completion: Completion, operation: str) -> None:
    meter.record(
        provider=completion.usage.provider,
        model=completion.usage.model,
        kind="llm",
        operation=operation,
        input_tokens=completion.usage.input_tokens,
        output_tokens=completion.usage.output_tokens,
    )
