import base64
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

Return a JSON object matching this schema:
{
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
Analyze this meeting transcript thoroughly.

First, reason about the purpose of this meeting.
Then extract key points, comprehensive notes (grouped by topic), and action items.

Return a JSON object matching this schema:
{
  "title": "Purpose-driven title, e.g. 'Weekly sync: Align on Q2 launch timeline'",
  "excerpt": "2-3 sentences: what happened and what was decided",
  "key_points": ["point 1", "point 2", "..."],
  "notes": {
    "Topic A": ["keyword: detail", "keyword: more detail", "sub-point", "..."],
    "Topic B": ["keyword: detail", "context", "..."]
  },
  "action_items": ["Task description (Owner: Speaker N)"]
}

Rules:
- Title should state the meeting's purpose, not just "Meeting Summary"
- Key points: 5-10 points covering all major topics discussed
- Notes are the most detailed section — capture everything important from the meeting
  - Use keyword/phrase style (not full sentences), e.g. "deadline: March 30", "blocker: API rate limit"
  - Include specifics: names, numbers, dates, decisions, concerns, alternatives discussed
  - Each topic cluster should have 5-15 bullets — be thorough, not minimal
  - Group by logical topic, not chronological order
  - It is OK to have many topics and many bullets — completeness over brevity
- Action items include owner when identifiable

Transcript:
%s
"""


class MeetingProcessor:
    """Core meeting processing pipeline.

    Orchestrates: download → extract audio → transcribe → summarize.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def process(self, drive_input: str, *, credentials: object | None = None, upload: bool = False) -> MeetingResult:
        """Run the full pipeline: download, extract audio, transcribe, summarize, and optionally upload.

        Args:
            drive_input: Google Drive URL or file ID.
            credentials: Optional explicit credentials (for server OAuth flow).
            upload: If True, upload the result to the same Drive folder as the source video.

        Returns:
            MeetingResult with transcript and summary.
        """
        logger.info("[1/4] Starting pipeline for: %s", drive_input)
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            file_id = parse_drive_input(drive_input)
            if credentials is None:
                credentials = resolve_credentials(self.settings)

            logger.info("[2/4] Downloading and extracting audio...")
            audio_path, filename = self._download_and_extract_audio(file_id, tmp_path, credentials=credentials)
            logger.info("[3/4] Transcribing...")
            transcript = self._transcribe_audio(audio_path)
            logger.info("[4/4] Summarizing...")
            summary = self._summarize(transcript)
            result = MeetingResult(transcript=transcript, summary=summary, source_filename=filename)

            if upload:
                logger.info("Uploading result to Drive...")
                self._upload_to_drive(file_id, filename, result, credentials)
            logger.info("Done!")
            return result

    def transcribe_only(
        self, drive_input: str, *, credentials: object | None = None, upload: bool = False
    ) -> MeetingResult:
        """Run the pipeline without the summary step.

        Args:
            drive_input: Google Drive URL or file ID.
            credentials: Optional explicit credentials (for server OAuth flow).
            upload: If True, upload the result to the same Drive folder as the source video.

        Returns:
            MeetingResult with transcript only.
        """
        logger.info("[1/3] Starting pipeline for: %s", drive_input)
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            file_id = parse_drive_input(drive_input)
            if credentials is None:
                credentials = resolve_credentials(self.settings)

            logger.info("[2/3] Downloading and extracting audio...")
            audio_path, filename = self._download_and_extract_audio(file_id, tmp_path, credentials=credentials)
            logger.info("[3/3] Transcribing...")
            transcript = self._transcribe_audio(audio_path)
            result = MeetingResult(transcript=transcript, source_filename=filename)

            if upload:
                logger.info("Uploading result to Drive...")
                self._upload_to_drive(file_id, filename, result, credentials)
            logger.info("Done!")
            return result

    def _download_and_extract_audio(self, file_id: str, tmp_dir: Path, *, credentials: object) -> tuple[Path, str]:
        video_path = download_file(file_id, credentials, tmp_dir)
        filename = video_path.name

        audio_path = tmp_dir / f"{video_path.stem}.mp3"
        logger.info("Extracting audio: %s → %s", video_path.name, audio_path.name)

        self._run_ffmpeg(video_path, audio_path)
        return audio_path, filename

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

    def _summarize(self, transcript: Transcript) -> Summary:
        logger.info("Summarizing %d segments with %s...", len(transcript.segments), self.settings.summary_model)

        transcript_md = transcript.to_markdown()
        prompt = SUMMARY_PROMPT % transcript_md

        response = litellm.completion(
            model=self.settings.summary_model,
            messages=[{"role": "user", "content": prompt}],
            response_format=Summary,
        )

        raw = response.choices[0].message.content
        logger.info("Summary complete")
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
