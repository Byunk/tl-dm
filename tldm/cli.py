import argparse
import logging
from pathlib import Path

from tldm.config import Settings
from tldm.processor import MeetingProcessor

logger = logging.getLogger(__name__)


def main() -> None:
    """Entry point for the tldm CLI."""
    parser = argparse.ArgumentParser(prog="tldm", description="Too Long; Did Meet")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # transcribe
    tx_parser = subparsers.add_parser("transcribe", help="Transcribe a meeting video")
    tx_parser.add_argument("source", help="Google Drive URL or file ID")
    tx_parser.add_argument("--model", "-m", help="Gemini model for transcription")
    tx_parser.add_argument("--upload", "-u", action="store_true", help="Upload results to the same Drive folder")

    # summarize
    sm_parser = subparsers.add_parser("summarize", help="Transcribe and summarize a meeting video")
    sm_parser.add_argument("source", help="Google Drive URL or file ID")
    sm_parser.add_argument("--model", "-m", help="Model for transcription")
    sm_parser.add_argument("--summary-model", help="Model for summary (defaults to --model)")
    sm_parser.add_argument("--upload", "-u", action="store_true", help="Upload results to the same Drive folder")

    args = parser.parse_args()
    _setup_logging(verbose=args.verbose)

    if args.command == "transcribe":
        _handle_transcribe(args)
    elif args.command == "summarize":
        _handle_summarize(args)


def _setup_logging(*, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _handle_transcribe(args: argparse.Namespace) -> None:
    settings = _build_settings(args)
    processor = MeetingProcessor(settings)

    result = processor.transcribe_only(args.source, upload=args.upload)
    stem = Path(result.source_filename).stem
    transcript_path = Path(f"{stem}_transcript.md")
    transcript_path.write_text(result.transcript.to_markdown())
    logger.info("Saved to %s", transcript_path)


def _handle_summarize(args: argparse.Namespace) -> None:
    settings = _build_settings(args)
    processor = MeetingProcessor(settings)

    result = processor.process(args.source, upload=args.upload)
    stem = Path(result.source_filename).stem

    transcript_path = Path(f"{stem}_transcript.md")
    transcript_path.write_text(result.transcript.to_markdown())
    logger.info("Saved transcript to %s", transcript_path)

    summary_path = Path(f"{stem}_summary.md")
    summary_path.write_text(result.summary.to_markdown())
    logger.info("Saved summary to %s", summary_path)


def _build_settings(args: argparse.Namespace) -> Settings:
    overrides = {}
    if getattr(args, "model", None):
        overrides["transcription_model"] = args.model
    if getattr(args, "summary_model", None):
        overrides["summary_model"] = args.summary_model
    return Settings(**overrides)
