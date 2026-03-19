import base64
import json
import logging
import tempfile
from pathlib import Path

import ffmpeg
import litellm

from tldm.config import Settings
from tldm.drive import download_file, get_file_parents, parse_drive_input, resolve_credentials, upload_file
from tldm.models import MeetingResult, Summary, Transcript

logger = logging.getLogger(__name__)

TRANSCRIPTION_PROMPT = """\
Transcribe this audio with speaker diarization.

Identify distinct speakers as Speaker 1, Speaker 2, etc.
Include timestamps for each segment.
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

SUMMARY_PROMPT = """\
You are an experienced meeting note taker. Write comprehensive, decision-ready
notes that capture not just what was said, but WHY.

Your audience is the meeting organizer who needs to:
- Recall every important detail without re-watching
- Make decisions based on these notes
- Share them with teammates who weren't present

Write ALL output in %s. Only JSON keys stay in English.

## Instructions

1. **Identify participants** by name from the transcript (people often introduce
   themselves). Use real names instead of "Speaker 1".
2. **Key points**: 5-10 high-level points covering the major topics discussed.
3. **Notes**: Group findings by topic, not chronologically.
   For each finding, capture:
   - The finding itself (what was said or observed)
   - The reasoning behind it (why — motivation, context, cause)
   - A verbatim quote if particularly notable (otherwise leave empty)
4. Include specific numbers, names, dates, examples, and anecdotes.
5. Write in full descriptive phrases, not keyword fragments.
6. **Key points** summarize what was discussed; **notes** go deep on each topic.
7. Action items include owner (by name) when identifiable.

Return a JSON object matching this schema:
{
  "title": "Purpose-driven title, not just 'Meeting Summary'",
  "excerpt": "2-3 sentences: what happened and what was decided",
  "key_points": ["High-level summary point"],
  "action_items": ["Task description (Owner: Name)"],
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
- Title should state the meeting's purpose
- Key points: 5-10 points covering all major topics
- Notes are the most detailed section — be thorough, not minimal
  - Write in full descriptive sentences, not keyword fragments
  - For each point, capture WHAT happened AND WHY (the reasoning or context)
  - Each topic should have 5-15 bullets — completeness over brevity
  - It is OK to have many topics and many bullets
- Action items include owner by name when identifiable

Transcript:
%s
"""


AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}


class MeetingProcessor:
    """Core meeting processing pipeline.

    Orchestrates: download → extract audio → transcribe → summarize.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def process(self, source: str, *, credentials: object | None = None, upload: bool = False) -> MeetingResult:
        """Run the full pipeline: download, extract audio, transcribe, summarize, and optionally upload.

        Args:
            source: Local file path, Google Drive URL, or file ID.
            credentials: Optional explicit credentials (for server OAuth flow).
            upload: If True, upload the result to the same Drive folder as the source video.

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
            transcript = self._transcribe_audio(audio_path)
            logger.info("[4/4] Summarizing...")
            summary = self._summarize(transcript)
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
            transcript = self._transcribe_audio(audio_path)
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

    def _transcribe_audio(self, audio_path: Path) -> Transcript:
        audio_size_mb = audio_path.stat().st_size / (1024 * 1024)
        logger.info("Encoding audio (%.1f MB) as base64...", audio_size_mb)
        audio_b64 = base64.b64encode(audio_path.read_bytes()).decode("utf-8")
        payload_mb = len(audio_b64) / (1024 * 1024)
        logger.info(
            "Sending %.1f MB payload to %s (this may take a few minutes)...",
            payload_mb,
            self.settings.transcription_model,
        )

        response = litellm.completion(
            model=self.settings.transcription_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": TRANSCRIPTION_PROMPT},
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
        logger.info("Transcription complete (%d chars)", len(raw))
        return Transcript.model_validate_json(raw)

    _SUMMARY_MAX_RETRIES = 3

    def _summarize(self, transcript: Transcript) -> Summary:
        """Summarize transcript with retry on incomplete output.

        Args:
            transcript: The meeting transcript to summarize.

        Returns:
            Summary with all sections populated.
        """
        logger.info("Summarizing %d segments with %s...", len(transcript.segments), self.settings.summary_model)

        language = transcript.language if transcript.language else "the same language as the transcript"
        transcript_md = transcript.to_markdown()
        prompt = SUMMARY_PROMPT % (language, transcript_md)

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
