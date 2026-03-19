from unittest.mock import Mock, patch

import pytest

from tldm.config import Settings
from tldm.models import Note, Section, Segment, Summary, Transcript
from tldm.processor import (
    MeetingProcessor,
    _format_timestamp,
    _parse_timestamp,
    _reinterpret_mmsscc,
)


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
        language="English",
        segments=[
            Segment(speaker="Speaker 1", start_time="00:00:00", end_time="00:00:10", text="Hello everyone"),
            Segment(speaker="Speaker 2", start_time="00:00:11", end_time="00:00:20", text="Hi, thanks for joining"),
        ],
    )


@pytest.fixture
def sample_summary():
    return Summary(
        title="Team Standup: Align on project timeline",
        excerpt="The team discussed the project timeline and assigned PR reviews.",
        key_points=["Discussed project timeline"],
        action_items=["Review PR (Owner: Alice)"],
        participants=["Alice — engineering lead", "Bob — PM"],
        notes=[
            Section(
                topic="Timeline",
                notes=[Note(finding="Deadline is Friday", reasoning="Client demo scheduled for Monday")],
            )
        ],
    )


class TestTimestampHelpers:
    def test_parse_and_format_roundtrip(self):
        for ts in ("00:00:00", "01:23:45", "02:00:00", "00:09:59"):
            assert _format_timestamp(_parse_timestamp(ts)) == ts

    def test_parse_two_components(self):
        assert _parse_timestamp("05:30") == 330

    def test_reinterpret_mmsscc(self):
        assert _reinterpret_mmsscc("05:30:15") == "00:05:30"
        assert _reinterpret_mmsscc("00:00:00") == "00:00:00"
        assert _reinterpret_mmsscc("10:16:04") == "00:10:16"


class TestMergeChunkTranscripts:
    def test_adjusts_timestamps_by_offset(self):
        chunk = Transcript(
            language="English",
            segments=[Segment(speaker="S1", start_time="00:00:10", end_time="00:00:20", text="hello")],
        )
        result = MeetingProcessor._merge_chunk_transcripts([(chunk, 570)])
        assert result.segments[0].start_time == "00:09:40"
        assert result.segments[0].end_time == "00:09:50"

    def test_deduplicates_overlapping_segments(self):
        chunk1 = Transcript(
            language="English",
            segments=[
                Segment(speaker="S1", start_time="00:09:00", end_time="00:09:30", text="end of chunk 1"),
                Segment(speaker="S2", start_time="00:09:31", end_time="00:10:00", text="last in chunk 1"),
            ],
        )
        chunk2 = Transcript(
            language="English",
            segments=[
                Segment(speaker="S1", start_time="00:00:00", end_time="00:00:20", text="overlap"),
                Segment(speaker="S2", start_time="00:00:31", end_time="00:01:00", text="new content"),
            ],
        )
        result = MeetingProcessor._merge_chunk_transcripts([(chunk1, 0), (chunk2, 570)])

        assert len(result.segments) == 3
        assert result.segments[-1].text == "new content"
        assert result.segments[-1].start_time == _format_timestamp(570 + 31)

    def test_reinterprets_mmsscc_format(self):
        """Chunk with timestamps parsed as HH:MM:SS but actually MM:SS:CC gets corrected."""
        chunk = Transcript(
            language="English",
            segments=[
                Segment(speaker="S1", start_time="08:30:00", end_time="09:00:00", text="late in chunk"),
            ],
        )
        # 08:30:00 parsed as HH:MM:SS = 30600s, way over 660s threshold → reinterpret
        # After reinterpret: 08:30 = 510s, formatted as 00:08:30
        # After offset 0: still 00:08:30
        result = MeetingProcessor._merge_chunk_transcripts([(chunk, 0)])
        assert result.segments[0].start_time == "00:08:30"

    def test_language_from_first_chunk(self):
        chunk1 = Transcript(language="Korean", segments=[])
        chunk2 = Transcript(language="English", segments=[])
        result = MeetingProcessor._merge_chunk_transcripts([(chunk1, 0), (chunk2, 570)])
        assert result.language == "Korean"


class TestMeetingProcessor:
    @pytest.fixture(autouse=True)
    def _mock_audio_duration(self):
        with patch.object(MeetingProcessor, "_get_audio_duration", return_value=120.0) as mock:
            self.mock_duration = mock
            yield

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
        chunk_summary_response = Mock(choices=[Mock(message=Mock(content="- Key point from chunk"))])
        synthesis_response = Mock(choices=[Mock(message=Mock(content=sample_summary.model_dump_json()))])
        mock_litellm.completion.side_effect = [transcript_response, chunk_summary_response, synthesis_response]

        with patch.object(MeetingProcessor, "_run_ffmpeg") as mock_ffmpeg:

            def create_audio(video_path, audio_path):
                audio_path.write_bytes(b"fake audio")

            mock_ffmpeg.side_effect = create_audio

            result = processor.process("file123_abcdefghijklmnop")

        assert result.summary is not None
        assert result.summary.title == "Team Standup: Align on project timeline"
        # 1 transcription + 1 chunk summary + 1 synthesis = 3
        assert mock_litellm.completion.call_count == 3

    @patch("tldm.processor.litellm")
    def test_transcribe_local_audio(self, mock_litellm, processor, sample_transcript, tmp_path):
        audio_file = tmp_path / "recording.mp3"
        audio_file.write_bytes(b"fake audio")

        mock_response = Mock(choices=[Mock(message=Mock(content=sample_transcript.model_dump_json()))])
        mock_litellm.completion.return_value = mock_response

        with patch.object(MeetingProcessor, "_run_ffmpeg") as mock_ffmpeg:
            result = processor.transcribe_only(str(audio_file))

        assert result.source_filename == "recording.mp3"
        mock_ffmpeg.assert_not_called()
        mock_litellm.completion.assert_called_once()

    @patch("tldm.processor.litellm")
    def test_long_audio_splits_into_chunks(self, mock_litellm, processor, tmp_path):
        """Audio longer than CHUNK_DURATION_SECS triggers chunking."""
        self.mock_duration.return_value = 1200.0
        audio_file = tmp_path / "long_meeting.mp3"
        audio_file.write_bytes(b"fake audio")

        chunk1_transcript = Transcript(
            language="English",
            segments=[
                Segment(speaker="Speaker 1", start_time="00:00:00", end_time="00:05:00", text="chunk 1 content"),
                Segment(speaker="Speaker 2", start_time="00:05:01", end_time="00:09:50", text="chunk 1 end"),
            ],
        )
        chunk2_transcript = Transcript(
            language="English",
            segments=[
                Segment(speaker="Speaker 1", start_time="00:00:00", end_time="00:00:20", text="overlap"),
                Segment(speaker="Speaker 2", start_time="00:00:30", end_time="00:05:00", text="chunk 2 content"),
            ],
        )

        responses = [
            Mock(choices=[Mock(message=Mock(content=chunk1_transcript.model_dump_json()))]),
            Mock(choices=[Mock(message=Mock(content=chunk2_transcript.model_dump_json()))]),
        ]
        mock_litellm.completion.side_effect = responses

        with patch.object(
            MeetingProcessor,
            "_split_audio",
            return_value=[(audio_file, 0), (audio_file, 570)],
        ):
            transcript, chunk_transcripts = processor._transcribe_audio(audio_file)

        assert mock_litellm.completion.call_count == 2
        assert len(transcript.segments) == 3
        assert transcript.language == "English"
        last_seg = transcript.segments[-1]
        assert last_seg.start_time == _format_timestamp(570 + 30)
        assert last_seg.text == "chunk 2 content"
        # Raw chunk transcripts preserved for summarization
        assert len(chunk_transcripts) == 2
