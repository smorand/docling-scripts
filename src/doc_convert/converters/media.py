"""Media converter: audio/video processing via external LLM."""

from __future__ import annotations

import logging
from pathlib import Path  # noqa: TC003

from doc_convert.base import BaseConverter
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
        options: ConvertOptions,  # noqa: F821
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
        else:
            from video import build_extraction_prompt  # noqa: PLC0415

            prompt, system = build_extraction_prompt(self.meeting)
            span_name = "video.extract"

        with trace_span(span_name, file=self.source.name, provider=provider):
            md = process_media(self.source, provider, model, prompt, api_key, system_prompt=system, url=url)
        title = _short_meeting_title(self.meeting)
        if title:
            md = f"# {title}\n\n{md}"
        self.write_document_md(md)
        print_output_summary(self.output_dir)

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
        (self.output_dir / "analysis.md").write_text(analysis_md)
        logger.info("Analysis written to %s/analysis.md", self.output_dir)
        return True
