from unittest.mock import Mock, patch

import pytest

from tldm.config import Settings
from tldm.models import Segment, Summary, Transcript
from tldm.processor import MeetingProcessor


@pytest.fixture
def settings():
    return Settings(
        transcription_model="gemini/gemini-2.5-flash-lite",
        summary_model="gemini/gemini-2.5-flash-lite",
    )


@pytest.fixture
def processor(settings):
    return MeetingProcessor(settings)


@pytest.fixture
def sample_transcript():
    return Transcript(
        segments=[
            Segment(speaker="Speaker 1", start_time="00:00:00", end_time="00:00:10", text="Hello everyone"),
            Segment(speaker="Speaker 2", start_time="00:00:11", end_time="00:00:20", text="Hi, thanks for joining"),
        ]
    )


@pytest.fixture
def sample_summary():
    return Summary(
        title="Team Standup: Align on project timeline",
        excerpt="The team discussed the project timeline and assigned PR reviews.",
        key_points=["Discussed project timeline"],
        notes={"Timeline": ["deadline: Friday", "blocker: API review"]},
        action_items=["Review PR (Owner: Speaker 1)"],
    )


class TestMeetingProcessor:
    @patch("tldm.processor.litellm")
    @patch("tldm.processor.download_file")
    @patch("tldm.processor.resolve_credentials")
    @patch("tldm.processor.parse_drive_input")
    def test_transcribe_only(
        self,
        mock_parse,
        mock_creds,
        mock_download,
        mock_litellm,
        processor,
        sample_transcript,
        tmp_path,
    ):
        mock_parse.return_value = "file123"
        mock_creds.return_value = Mock()

        video_file = tmp_path / "meeting.mp4"
        video_file.write_bytes(b"fake video")
        mock_download.return_value = video_file

        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content=sample_transcript.model_dump_json()))]
        mock_litellm.completion.return_value = mock_response

        with patch.object(MeetingProcessor, "_run_ffmpeg") as mock_ffmpeg:

            def create_audio(video_path, audio_path):
                audio_path.write_bytes(b"fake audio")

            mock_ffmpeg.side_effect = create_audio

            result = processor.transcribe_only("https://drive.google.com/file/d/file123/view")

        assert result.source_filename == "meeting.mp4"
        assert len(result.transcript.segments) == 2
        assert result.summary is None
        mock_litellm.completion.assert_called_once()

    @patch("tldm.processor.litellm")
    @patch("tldm.processor.download_file")
    @patch("tldm.processor.resolve_credentials")
    @patch("tldm.processor.parse_drive_input")
    def test_process_with_summary(
        self,
        mock_parse,
        mock_creds,
        mock_download,
        mock_litellm,
        processor,
        sample_transcript,
        sample_summary,
        tmp_path,
    ):
        mock_parse.return_value = "file123"
        mock_creds.return_value = Mock()

        video_file = tmp_path / "meeting.mp4"
        video_file.write_bytes(b"fake video")
        mock_download.return_value = video_file

        transcript_response = Mock(choices=[Mock(message=Mock(content=sample_transcript.model_dump_json()))])
        summary_response = Mock(choices=[Mock(message=Mock(content=sample_summary.model_dump_json()))])
        mock_litellm.completion.side_effect = [transcript_response, summary_response]

        with patch.object(MeetingProcessor, "_run_ffmpeg") as mock_ffmpeg:

            def create_audio(video_path, audio_path):
                audio_path.write_bytes(b"fake audio")

            mock_ffmpeg.side_effect = create_audio

            result = processor.process("file123_abcdefghijklmnop")

        assert result.summary is not None
        assert result.summary.title == "Team Standup: Align on project timeline"
        assert mock_litellm.completion.call_count == 2
