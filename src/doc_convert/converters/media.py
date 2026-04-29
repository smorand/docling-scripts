"""Media converter: audio/video processing via external LLM."""

from __future__ import annotations

import logging
from pathlib import Path  # noqa: TC003

from doc_convert.base import BaseConverter
from doc_convert.output import print_output_summary

logger = logging.getLogger(__name__)


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
        use_external_llm: str | None = None,
    ) -> None:
        super().__init__(source, options)
        self.media_type = media_type
        self.meeting = meeting
        self.instructions = instructions
        self.lang = lang
        self.use_external_llm = use_external_llm

    def convert(self) -> None:
        from doc_convert.providers import resolve_media_llm  # noqa: PLC0415
        from media_llm import process_media  # noqa: PLC0415
        from tracing import trace_span  # noqa: PLC0415

        self.ensure_output_dir()
        provider, model, api_key = resolve_media_llm(self.use_external_llm, self.options.settings)

        if self.media_type == "audio":
            from audio import build_transcription_prompt  # noqa: PLC0415

            prompt, system = build_transcription_prompt(self.meeting)
            span_name = "audio.transcribe"
        else:
            from video import build_extraction_prompt  # noqa: PLC0415

            prompt, system = build_extraction_prompt(self.meeting)
            span_name = "video.extract"

        with trace_span(span_name, file=self.source.name, provider=provider):
            md = process_media(self.source, provider, model, prompt, api_key, system_prompt=system)
        if self.meeting:
            md = f"# {self.meeting}\n\n{md}"
        self.write_document_md(md)
        print_output_summary(self.output_dir)

    def run_analysis(  # type: ignore[override]
        self,
        use_external_llm: str | None,
        instructions: str | None = None,
        meeting: str | None = None,
        lang: str | None = None,
    ) -> bool:
        """Run media-specific analysis on the transcribed document.

        Returns True if analysis was written.
        """
        from doc_convert.providers import resolve_media_llm  # noqa: PLC0415
        from media_llm import process_media  # noqa: PLC0415
        from tracing import trace_span  # noqa: PLC0415

        if not (self.output_dir / "document.md").exists():
            return False

        provider, model, api_key = resolve_media_llm(use_external_llm, self.options.settings)
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
            a_system = f"IMPORTANT: Write your entire response in {lang}.\n\n{a_system}"

        with trace_span(f"{self.media_type}.analyze", file=self.source.name, provider=provider):
            analysis_md = process_media(self.source, provider, model, a_prompt, api_key, system_prompt=a_system)
        if meeting_ctx:
            analysis_md = f"# {meeting_ctx}\n\n{analysis_md}"
        (self.output_dir / "analysis.md").write_text(analysis_md)
        logger.info("Analysis written to %s/analysis.md", self.output_dir)
        return True
