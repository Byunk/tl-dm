import base64
import json
import logging
import tempfile
from pathlib import Path

import ffmpeg
import litellm

from tldm.config import Settings
from tldm.drive import download_file, get_file_parents, parse_drive_input, resolve_credentials, upload_file
from tldm.models import MeetingResult, Segment, Summary, Transcript

logger = logging.getLogger(__name__)

CHUNK_DURATION_SECS = 600  # 10 minutes
CHUNK_OVERLAP_SECS = 30  # 30 seconds

TRANSCRIPTION_PROMPT = """\
Transcribe this audio with speaker diarization.

Identify distinct speakers as Speaker 1, Speaker 2, etc.
Include timestamps in HH:MM:SS format (hours:minutes:seconds). Example: 00:05:30
means five minutes and thirty seconds. Always use this format even for short audio.
Preserve the original language — do not translate.
Detect the language being spoken and return it in the "language" field.

Return a JSON object matching this schema:
{
  "language": "Korean",
  "segments": [
    {
      "speaker": "Speaker 1",
      "start_time": "00:00:00",
      "end_time": "00:00:15",
      "text": "transcribed text here"
    }
  ]
}
"""

TRANSCRIPTION_CONTINUATION_PROMPT = """\
Transcribe this audio with speaker diarization.

This is a continuation of a longer recording. Here are the last few lines
from the previous segment — use the SAME speaker labels as below:
%s

Include timestamps in HH:MM:SS format (hours:minutes:seconds). Example: 00:05:30
means five minutes and thirty seconds. Timestamps start at 00:00:00 for this clip.
Preserve the original language — do not translate.
Return the language in the "language" field.

Return a JSON object matching this schema:
{
  "language": "Korean",
  "segments": [
    {
      "speaker": "Speaker 1",
      "start_time": "00:00:00",
      "end_time": "00:00:15",
      "text": "transcribed text here"
    }
  ]
}
"""

CHUNK_SUMMARY_PROMPT = """\
Summarize the following transcript section. Write in %s.

For each distinct topic discussed, capture:
- The key point or finding
- The reasoning or context behind it
- A notable verbatim quote (if any)
- Any action items mentioned, if applicable (with owner name if identifiable)

Also identify all participants by name if they introduce themselves.

Be thorough — this is one section of a longer recording. Capture every topic,
specific numbers, names, dates, examples, and anecdotes. Do not omit details
to save space.

Transcript section:
%s
"""

SYNTHESIS_PROMPT = """\
You are synthesizing section-by-section summaries of a long recording into
one comprehensive set of meeting notes. Write ALL output in %s. Only JSON
keys stay in English.

The section summaries below were produced independently. Your job:
1. Merge them into a single coherent summary — deduplicate, group by topic.
2. Identify all participants across all sections.
3. Combine action items if any exist (omit if none).
4. Write 5-10 high-level key points that span the entire recording.
5. Notes should be grouped by topic, not by section order.

Return a JSON object matching this schema:
{
  "title": "Purpose-driven title, not just 'Meeting Summary'",
  "excerpt": "2-3 sentences: what happened and what was decided",
  "key_points": ["High-level summary point"],
  "action_items": ["Task description (Owner: Name) — optional, omit if none"],
  "participants": ["Name — role/background"],
  "notes": [
    {
      "topic": "Topic name",
      "notes": [
        {
          "finding": "What was said or observed",
          "reasoning": "Why — the motivation or context behind it",
          "quote": "Notable verbatim quote, or empty string if none"
        }
      ]
    }
  ]
}

Rules:
- Key points: 5-10 points covering all major topics across all sections
- Notes: be thorough — each topic should have 5-15 bullets
- Deduplicate topics that appear in multiple sections
- Preserve specific numbers, names, dates, examples, and verbatim quotes
- Action items include owner by name when identifiable

Section summaries:
%s
"""


AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}


def _parse_timestamp(ts: str) -> int:
    parts = ts.split(":")
    if len(parts) == 3:
        a, b, c = int(parts[0]), int(parts[1]), int(parts[2])
        return a * 3600 + b * 60 + c
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return int(parts[0])


def _format_timestamp(seconds: int) -> str:
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _reinterpret_mmsscc(ts: str) -> str:
    """Reinterpret a MM:SS:CC timestamp as HH:MM:SS (dropping sub-second part)."""
    parts = ts.split(":")
    mm, ss = int(parts[0]), int(parts[1])
    return _format_timestamp(mm * 60 + ss)


class MeetingProcessor:
    """Core meeting processing pipeline.

    Orchestrates: download → extract audio → transcribe → summarize.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def process(
        self,
        source: str,
        *,
        credentials: object | None = None,
        upload: bool = False,
        context: str | None = None,
    ) -> MeetingResult:
        """Run the full pipeline: download, extract audio, transcribe, summarize, and optionally upload.

        Args:
            source: Local file path, Google Drive URL, or file ID.
            credentials: Optional explicit credentials (for server OAuth flow).
            upload: If True, upload the result to the same Drive folder as the source video.
            context: Optional context about the meeting to guide summarization.

        Returns:
            MeetingResult with transcript and summary.
        """
        logger.info("[1/4] Starting pipeline for: %s", source)
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            if Path(source).is_file():
                source_path = Path(source)
                filename = source_path.name
                file_id = None
            else:
                file_id = parse_drive_input(source)
                if credentials is None:
                    credentials = resolve_credentials(self.settings)
                logger.info("[2/4] Downloading from Drive...")
                source_path = download_file(file_id, credentials, tmp_path)
                filename = source_path.name

            audio_path = self._extract_audio(source_path, tmp_path)
            logger.info("[3/4] Transcribing...")
            transcript, chunk_transcripts = self._transcribe_audio(audio_path)
            logger.info("[4/4] Summarizing...")
            summary = self._summarize(chunk_transcripts, context=context)
            result = MeetingResult(transcript=transcript, summary=summary, source_filename=filename)

            if upload and file_id:
                logger.info("Uploading result to Drive...")
                self._upload_to_drive(file_id, filename, result, credentials)
            elif upload:
                logger.warning("Upload requires a Google Drive source — skipping upload for local file")
            logger.info("Done!")
            return result

    def transcribe_only(self, source: str, *, credentials: object | None = None, upload: bool = False) -> MeetingResult:
        """Run the pipeline without the summary step.

        Args:
            source: Local file path, Google Drive URL, or file ID.
            credentials: Optional explicit credentials (for server OAuth flow).
            upload: If True, upload the result to the same Drive folder as the source video.

        Returns:
            MeetingResult with transcript only.
        """
        logger.info("[1/3] Starting pipeline for: %s", source)
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            if Path(source).is_file():
                source_path = Path(source)
                filename = source_path.name
                file_id = None
            else:
                file_id = parse_drive_input(source)
                if credentials is None:
                    credentials = resolve_credentials(self.settings)
                logger.info("[2/3] Downloading from Drive...")
                source_path = download_file(file_id, credentials, tmp_path)
                filename = source_path.name

            audio_path = self._extract_audio(source_path, tmp_path)
            logger.info("[3/3] Transcribing...")
            transcript, _ = self._transcribe_audio(audio_path)
            result = MeetingResult(transcript=transcript, source_filename=filename)

            if upload and file_id:
                logger.info("Uploading result to Drive...")
                self._upload_to_drive(file_id, filename, result, credentials)
            elif upload:
                logger.warning("Upload requires a Google Drive source — skipping upload for local file")
            logger.info("Done!")
            return result

    def _extract_audio(self, source_path: Path, tmp_dir: Path) -> Path:
        if source_path.suffix.lower() in AUDIO_EXTENSIONS:
            logger.info("Source is already audio: %s", source_path.name)
            return source_path

        audio_path = tmp_dir / f"{source_path.stem}.mp3"
        logger.info("Extracting audio: %s → %s", source_path.name, audio_path.name)
        self._run_ffmpeg(source_path, audio_path)
        return audio_path

    @staticmethod
    def _upload_to_drive(file_id: str, source_filename: str, result: MeetingResult, credentials: object) -> None:
        parents = get_file_parents(file_id, credentials)
        if not parents:
            logger.warning("Could not determine parent folder, skipping Drive upload")
            return

        parent_id = parents[0]
        stem = Path(source_filename).stem

        upload_file(result.transcript.to_markdown(), f"{stem}_transcript.md", parent_id, credentials)
        if result.summary:
            upload_file(result.summary.to_markdown(), f"{stem}_summary.md", parent_id, credentials)

    def _transcribe_audio(self, audio_path: Path) -> tuple[Transcript, list[Transcript]]:
        """Transcribe audio, chunking automatically for long files.

        Returns:
            Tuple of (merged_transcript, chunk_transcripts). For short audio,
            chunk_transcripts is a single-element list.
        """
        duration = self._get_audio_duration(audio_path)
        logger.info("Audio duration: %s", _format_timestamp(int(duration)))

        if duration <= CHUNK_DURATION_SECS:
            transcript = self._transcribe_chunk(audio_path, TRANSCRIPTION_PROMPT)
            return transcript, [transcript]

        chunks = self._split_audio(audio_path, duration)
        total = len(chunks)
        logger.info("Split into %d chunks of %d min each", total, CHUNK_DURATION_SECS // 60)

        raw_transcripts: list[tuple[Transcript, int]] = []
        prev_tail: list[Segment] = []

        for i, (chunk_path, offset) in enumerate(chunks):
            logger.info("Transcribing chunk %d/%d (offset %s)...", i + 1, total, _format_timestamp(offset))

            if i == 0:
                prompt = TRANSCRIPTION_PROMPT
            else:
                context_lines = "\n".join(f"{seg.speaker}: {seg.text}" for seg in prev_tail)
                prompt = TRANSCRIPTION_CONTINUATION_PROMPT % context_lines

            transcript = self._transcribe_chunk(chunk_path, prompt)
            prev_tail = transcript.segments[-5:] if transcript.segments else []
            raw_transcripts.append((transcript, offset))

        chunk_transcripts = [t for t, _ in raw_transcripts]
        merged = self._merge_chunk_transcripts(raw_transcripts)
        return merged, chunk_transcripts

    @staticmethod
    def _merge_chunk_transcripts(chunks: list[tuple[Transcript, int]]) -> Transcript:
        """Merge per-chunk transcripts into one, adjusting timestamps and deduplicating overlap.

        Args:
            chunks: List of (transcript, offset_seconds) tuples.

        Returns:
            Single merged Transcript with absolute timestamps.
        """
        all_segments: list[Segment] = []
        language = ""

        for i, (transcript, offset) in enumerate(chunks):
            if i == 0:
                language = transcript.language

            # Detect MM:SS:CC format: if max timestamp exceeds chunk duration,
            # reinterpret as minutes:seconds (drop sub-second component)
            if transcript.segments:
                max_ts = max(_parse_timestamp(seg.end_time) for seg in transcript.segments)
                if max_ts > CHUNK_DURATION_SECS + 60:
                    logger.info("Detected MM:SS:CC timestamps in chunk %d, reinterpreting...", i)
                    for seg in transcript.segments:
                        seg.start_time = _reinterpret_mmsscc(seg.start_time)
                        seg.end_time = _reinterpret_mmsscc(seg.end_time)

            # Adjust timestamps to absolute
            for seg in transcript.segments:
                seg.start_time = _format_timestamp(_parse_timestamp(seg.start_time) + offset)
                seg.end_time = _format_timestamp(_parse_timestamp(seg.end_time) + offset)

            # Deduplicate overlap: drop segments that overlap with previous chunk
            if all_segments:
                last_end = _parse_timestamp(all_segments[-1].end_time)
                transcript.segments = [
                    seg for seg in transcript.segments if _parse_timestamp(seg.start_time) > last_end
                ]

            all_segments.extend(transcript.segments)

        logger.info("Merged %d segments from %d chunks", len(all_segments), len(chunks))
        return Transcript(segments=all_segments, language=language)

    def _transcribe_chunk(self, audio_path: Path, prompt: str) -> Transcript:
        audio_size_mb = audio_path.stat().st_size / (1024 * 1024)
        logger.info("Encoding audio (%.1f MB) as base64...", audio_size_mb)
        audio_b64 = base64.b64encode(audio_path.read_bytes()).decode("utf-8")

        response = litellm.completion(
            model=self.settings.transcription_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "input_audio",
                            "input_audio": {"data": audio_b64, "format": "mp3"},
                        },
                    ],
                }
            ],
            response_format=Transcript,
        )

        raw = response.choices[0].message.content
        return Transcript.model_validate_json(raw)

    @staticmethod
    def _get_audio_duration(audio_path: Path) -> float:
        probe = ffmpeg.probe(str(audio_path))
        return float(probe["format"]["duration"])

    @staticmethod
    def _split_audio(audio_path: Path, duration: float) -> list[tuple[Path, int]]:
        """Split audio into overlapping chunks.

        Args:
            audio_path: Path to the audio file.
            duration: Audio duration in seconds (avoids redundant ffmpeg.probe).

        Returns:
            List of (chunk_path, offset_seconds) tuples.
        """
        step = CHUNK_DURATION_SECS - CHUNK_OVERLAP_SECS
        chunks = []

        offset = 0
        chunk_idx = 0
        while offset < duration:
            chunk_path = audio_path.parent / f"chunk_{chunk_idx:03d}.mp3"
            stream = ffmpeg.input(str(audio_path), ss=offset, t=CHUNK_DURATION_SECS)
            stream = stream.output(str(chunk_path), ac=1, ar=16000, ab="32k")
            stream.overwrite_output().run(quiet=True)
            chunks.append((chunk_path, offset))
            offset += step
            chunk_idx += 1

        return chunks

    _SUMMARY_MAX_RETRIES = 3

    def _summarize(self, chunk_transcripts: list[Transcript], *, context: str | None = None) -> Summary:
        """Summarize using map-reduce: summarize each chunk, then synthesize.

        Args:
            chunk_transcripts: Per-chunk transcripts from the transcription step.
            context: Optional context about the meeting to guide summarization.

        Returns:
            Summary with all sections populated.
        """
        language = (
            chunk_transcripts[0].language if chunk_transcripts[0].language else "the same language as the transcript"
        )
        total_segments = sum(len(t.segments) for t in chunk_transcripts)

        # Map: summarize each chunk independently for focused extraction
        logger.info(
            "Summarizing %d segments in %d sections with %s...",
            total_segments,
            len(chunk_transcripts),
            self.settings.summary_model,
        )
        chunk_summaries: list[str] = []
        for i, chunk_transcript in enumerate(chunk_transcripts):
            logger.info("Summarizing section %d/%d...", i + 1, len(chunk_transcripts))
            chunk_md = chunk_transcript.to_markdown()
            prompt = CHUNK_SUMMARY_PROMPT % (language, chunk_md)
            if context:
                prompt = f"Context about this meeting: {context}\n\n{prompt}"

            response = litellm.completion(
                model=self.settings.summary_model,
                messages=[{"role": "user", "content": prompt}],
            )
            chunk_summaries.append(response.choices[0].message.content)

        # Reduce: synthesize chunk summaries into final structured Summary
        logger.info("Synthesizing %d section summaries...", len(chunk_summaries))
        combined = "\n\n---\n\n".join(f"## Section {i + 1}\n{s}" for i, s in enumerate(chunk_summaries))
        synthesis_prompt = SYNTHESIS_PROMPT % (language, combined)
        if context:
            synthesis_prompt = f"Context about this meeting: {context}\n\n{synthesis_prompt}"

        return self._summarize_with_retry(synthesis_prompt)

    def _summarize_with_retry(self, prompt: str) -> Summary:
        for attempt in range(1, self._SUMMARY_MAX_RETRIES + 1):
            response = litellm.completion(
                model=self.settings.summary_model,
                messages=[{"role": "user", "content": prompt}],
                response_format=Summary,
                reasoning_effort="medium",
                max_tokens=65536,
            )

            raw = response.choices[0].message.content
            try:
                json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "Summary attempt %d/%d returned truncated JSON (%d chars), retrying...",
                    attempt,
                    self._SUMMARY_MAX_RETRIES,
                    len(raw),
                )
                continue

            summary = Summary.model_validate_json(raw)

            if summary.notes:
                logger.info("Summary complete")
                return summary

            logger.warning(
                "Summary attempt %d/%d returned empty notes, retrying...", attempt, self._SUMMARY_MAX_RETRIES
            )

        logger.warning(
            "All %d summary attempts returned incomplete output, returning best effort", self._SUMMARY_MAX_RETRIES
        )
        return Summary.model_validate_json(raw)

    @staticmethod
    def _run_ffmpeg(video_path: Path, audio_path: Path) -> None:
        try:
            ffmpeg.input(str(video_path)).output(
                str(audio_path), vn=None, ac=1, ar=16000, ab="32k"
            ).overwrite_output().run(quiet=True)
        except FileNotFoundError:
            msg = "ffmpeg is not installed. Install it with: brew install ffmpeg (macOS) or apt install ffmpeg (Linux)"
            raise FileNotFoundError(msg) from None
        except ffmpeg.Error as e:
            msg = f"ffmpeg failed: {e.stderr.decode() if e.stderr else 'unknown error'}"
            raise RuntimeError(msg) from e
