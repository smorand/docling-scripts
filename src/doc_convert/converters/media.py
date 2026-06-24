"""Media converter: audio/video processing via external LLM."""

from __future__ import annotations

import logging
from pathlib import Path  # noqa: TC003

from doc_convert.base import BaseConverter, ConvertOptions
from doc_convert.output import print_output_summary

logger = logging.getLogger(__name__)

# When the "meeting" arg is short, it's a label we can safely prepend as an H1
# title. When it's long (e.g. the companion context has been merged into it),
# we must NOT prepend it — it would duplicate the companion at the top of the
# output. Threshold picked to fit a long meeting title but reject any document.
_MEETING_TITLE_MAX_LEN = 200


def _short_meeting_title(meeting: str | None) -> str | None:
    if not meeting:
        return None
    first_line = meeting.strip().splitlines()[0]
    if len(meeting) > _MEETING_TITLE_MAX_LEN or "\n\n" in meeting:
        return None
    return first_line


def _collect_companion_attachments(source: Path) -> list[Path]:
    """Find image attachments referenced in the companion .md next to ``source``.

    Returns image files (PNG/JPG/...) that the companion notes reference, so they
    can be sent alongside the audio/video in the multimodal LLM call. Returns an
    empty list if no companion exists or no images are referenced.
    """
    from doc_convert.companion import detect_companion, resolve_reference_paths  # noqa: PLC0415
    from media_llm import is_image_ext  # noqa: PLC0415

    companion = detect_companion(source)
    if companion is None:
        return []
    try:
        refs = resolve_reference_paths(companion)
    except Exception:
        logger.warning("Failed to resolve companion attachments for %s", companion.name)
        return []
    return [p for p in refs if is_image_ext(p.suffix)]


class MediaConverter(BaseConverter):
    """Audio/video processing via external LLM."""

    def __init__(
        self,
        source: Path,
        options: ConvertOptions,
        *,
        media_type: str,
        meeting: str | None = None,
        instructions: str | None = None,
        lang: str | None = None,
        llm: str | None = None,
    ) -> None:
        super().__init__(source, options)
        self.media_type = media_type
        self.meeting = meeting
        self.instructions = instructions
        self.lang = lang
        self.llm = llm

    def convert(self) -> None:
        from doc_convert.providers import get_provider_url, resolve_media_llm  # noqa: PLC0415
        from media_llm import process_media  # noqa: PLC0415
        from tracing import trace_span  # noqa: PLC0415

        self.ensure_output_dir()
        provider, model, api_key = resolve_media_llm(self.llm, self.options.settings)
        url = get_provider_url(provider, self.options.settings) if provider != "google" else None

        if self.media_type == "audio":
            from audio import build_transcription_prompt  # noqa: PLC0415

            prompt, system = build_transcription_prompt(self.meeting)
            span_name = "audio.transcribe"
            with trace_span(span_name, file=self.source.name, provider=provider):
                md = process_media(self.source, provider, model, prompt, api_key, system_prompt=system, url=url)
            md = self._assemble_audio_sections(md)
        else:
            md = self._extract_video_content(provider, model, api_key, url)

        title = _short_meeting_title(self.meeting)
        if title:
            md = f"# {title}\n\n{md}"
        self.write_document_md(md)
        print_output_summary(self.output_dir)

    def _extract_video_content(self, provider: str, model: str, api_key: str, url: str | None) -> str:
        """Run video extraction, chunking the source past CHUNK_THRESHOLD_SECONDS."""
        from media_llm import process_media  # noqa: PLC0415
        from tracing import trace_span  # noqa: PLC0415
        from video import (  # noqa: PLC0415
            CHUNK_THRESHOLD_SECONDS,
            build_extraction_prompt,
            chunk_video,
            format_timestamp,
            get_meta_summary_prompt,
            video_duration_seconds,
        )

        prompt, system = build_extraction_prompt(self.meeting)
        duration = video_duration_seconds(self.source)

        if duration is None or duration <= CHUNK_THRESHOLD_SECONDS:
            with trace_span("video.extract", file=self.source.name, provider=provider):
                return process_media(self.source, provider, model, prompt, api_key, system_prompt=system, url=url)

        chunk_paths = chunk_video(self.source)
        chunk_summaries: list[str] = []
        for i, chunk in enumerate(chunk_paths):
            start_s = i * (duration / max(1, len(chunk_paths)))
            with trace_span("video.extract.chunk", file=chunk.name, provider=provider, chunk=i + 1):
                logger.info("Processing chunk %d/%d (~%s)", i + 1, len(chunk_paths), format_timestamp(start_s))
                chunk_summaries.append(
                    process_media(chunk, provider, model, prompt, api_key, system_prompt=system, url=url)
                )

        # Meta-summary: text-only call (no need to resend the video)
        joined_chunks = "\n\n".join(
            f"### Chunk {i + 1} (starting ~{format_timestamp(i * CHUNK_THRESHOLD_SECONDS)})\n\n{summary}"
            for i, summary in enumerate(chunk_summaries)
        )
        with trace_span("video.meta_summary", file=self.source.name, provider=provider):
            meta = self._text_completion(
                provider,
                model,
                api_key,
                get_meta_summary_prompt(),
                f"Per-chunk summaries of the video:\n\n{joined_chunks}",
            )

        parts: list[str] = ["## Executive Summary", "", meta.strip(), ""]
        for i, summary in enumerate(chunk_summaries):
            start_s = i * CHUNK_THRESHOLD_SECONDS
            end_s = min((i + 1) * CHUNK_THRESHOLD_SECONDS, duration)
            parts.extend(
                [
                    f"## Chunk {i + 1}: {format_timestamp(start_s)} to {format_timestamp(end_s)}",
                    "",
                    summary.strip(),
                    "",
                ]
            )
        return "\n".join(parts)

    def _text_completion(self, provider: str, model: str, api_key: str, system: str, user: str) -> str:
        """Plain text-only chat completion (used for video meta-summary)."""
        import httpx  # noqa: PLC0415

        from doc_convert.providers import get_provider_url  # noqa: PLC0415

        with httpx.Client(timeout=180.0) as client:
            resp = client.post(
                get_provider_url(provider, self.options.settings),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]  # type: ignore[no-any-return]

    def _assemble_audio_sections(self, transcription_md: str) -> str:
        """Append Additional Context, Screenshots and Additional Documents sections.

        Reads the companion .md next to the audio source (when present), splits
        its referenced files into images vs text documents, and emits structured
        sections that make ``document.md`` self-sufficient for downstream
        ``--analyze`` and ``--meeting-summary`` passes.
        """
        from doc_convert.companion import detect_companion, resolve_reference_paths  # noqa: PLC0415
        from doc_convert.recursive import build_attachments_section, convert_children  # noqa: PLC0415
        from media_llm import is_image_ext  # noqa: PLC0415

        companion = detect_companion(self.source)
        if companion is None:
            return transcription_md

        parts: list[str] = [transcription_md.rstrip()]
        try:
            companion_text = companion.read_text().strip()
        except OSError:
            companion_text = ""
        if companion_text:
            parts.extend(["", "## Additional Context", "", companion_text])

        try:
            refs = resolve_reference_paths(companion)
        except Exception:
            logger.warning("Failed to resolve companion references for %s", companion.name)
            refs = []
        image_refs = [p for p in refs if is_image_ext(p.suffix)]
        doc_refs = [p for p in refs if not is_image_ext(p.suffix)]

        if image_refs and self.options.captions_enabled:
            screenshots_dir = self.output_dir / "screenshots"
            screenshots_dir.mkdir(exist_ok=True)
            local_imgs: list[Path] = []
            local_refs: list[str] = []
            for img in image_refs:
                dest = screenshots_dir / img.name
                dest.write_bytes(img.read_bytes())
                local_imgs.append(dest)
                local_refs.append(f"#/screenshots/{img.stem}")
            descriptions = self.describe_figures(local_imgs, local_refs, "vlm.describe_audio_screenshots")
            parts.extend(["", "## Screenshots", ""])
            for path, ref in zip(local_imgs, local_refs, strict=True):
                parts.append(f"### {path.name}")
                parts.append("")
                desc = descriptions.get(ref, "")
                if desc:
                    parts.append(desc.strip())
                    parts.append("")
                parts.append(f"*Image: [`screenshots/{path.name}`](screenshots/{path.name})*")
                parts.append("")

        if doc_refs:
            ad_dir = self.output_dir / "additional_documents"
            ad_dir.mkdir(exist_ok=True)
            local_docs: list[Path] = []
            for doc in doc_refs:
                dest = ad_dir / doc.name
                dest.write_bytes(doc.read_bytes())
                local_docs.append(dest)
            entries = convert_children(local_docs, self.options.settings, llm=self.llm)
            section = build_attachments_section(entries, heading="Additional Documents")
            if section:
                parts.extend(["", section])

        return "\n".join(parts)

    def run_analysis(  # type: ignore[override]
        self,
        llm: str | None,
        instructions: str | None = None,
        meeting: str | None = None,
        lang: str | None = None,
    ) -> bool:
        """Run media-specific analysis on the transcribed document.

        Returns True if analysis was written.
        """
        from doc_convert.providers import get_provider_url, resolve_media_llm  # noqa: PLC0415
        from media_llm import process_media  # noqa: PLC0415
        from tracing import trace_span  # noqa: PLC0415

        if not (self.output_dir / "document.md").exists():
            return False

        provider, model, api_key = resolve_media_llm(llm, self.options.settings)
        url = get_provider_url(provider, self.options.settings) if provider != "google" else None
        meeting_ctx = meeting or self.meeting

        a_prompt: str
        a_system: str | None
        if instructions:
            a_prompt = instructions
            a_system = f"Context: {meeting_ctx}\n\n{instructions}" if meeting_ctx else instructions
        elif self.media_type == "audio":
            from audio import build_analysis_prompt as audio_analysis  # noqa: PLC0415

            a_prompt, a_system = audio_analysis(meeting_ctx)
        else:
            from video import build_analysis_prompt as video_analysis  # noqa: PLC0415

            a_prompt, a_system = video_analysis(meeting_ctx)

        if lang:
            lang_rule = (
                f"HARD RULE: Write the ENTIRE response in {lang}, including all section headings, "
                f"table headers, and content. Do NOT switch language even if the source material or "
                f"injected context is in another language."
            )
            a_system = f"{lang_rule}\n\n{a_system}\n\n{lang_rule}"

        attachments = _collect_companion_attachments(self.source)
        if attachments:
            names = ", ".join(a.name for a in attachments)
            logger.info("Attaching %d companion image(s) to analysis call: %s", len(attachments), names)
            a_system = (
                f"{a_system}\n\nATTACHED IMAGES (in order): "
                + ", ".join(f"{i + 1}. {a.name}" for i, a in enumerate(attachments))
                + "\nCite them in the output as '(from whiteboard, image NN)' using the order above."
            )

        with trace_span(f"{self.media_type}.analyze", file=self.source.name, provider=provider):
            analysis_md = process_media(
                self.source,
                provider,
                model,
                a_prompt,
                api_key,
                system_prompt=a_system,
                url=url,
                attachments=attachments or None,
            )
        title = _short_meeting_title(meeting_ctx)
        if title:
            analysis_md = f"# {title}\n\n{analysis_md}"
        (self.output_dir / "analyze.md").write_text(analysis_md)
        logger.info("Analysis written to %s/analyze.md", self.output_dir)
        return True
