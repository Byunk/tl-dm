from pydantic import BaseModel


class Segment(BaseModel):
    """A single speech segment from the transcript."""

    speaker: str
    start_time: str
    end_time: str
    text: str = ""


class Transcript(BaseModel):
    """Full meeting transcript with speaker-labeled segments."""

    segments: list[Segment]
    language: str = ""

    def to_markdown(self) -> str:
        """Format transcript as markdown with speaker labels and timestamps.

        Returns:
            Markdown-formatted transcript string.
        """
        lines = []
        for seg in self.segments:
            lines.append(f"**{seg.speaker}** ({seg.start_time} - {seg.end_time})")
            lines.append(f"{seg.text}\n")
        return "\n".join(lines)


class Summary(BaseModel):
    """Meeting summary with purpose-driven structure."""

    title: str
    excerpt: str
    key_points: list[str]
    notes: dict[str, list[str]]
    action_items: list[str]

    def to_markdown(self) -> str:
        """Format summary as markdown.

        Returns:
            Markdown-formatted summary string.
        """
        lines = [f"# {self.title}\n"]
        lines.append(f"{self.excerpt}\n")

        lines.append("## Key Points\n")
        lines.extend(f"- {point}" for point in self.key_points)

        if self.notes:
            lines.append("\n## Notes\n")
            for topic, bullets in self.notes.items():
                lines.append(f"### {topic}\n")
                lines.extend(f"- {b}" for b in bullets)
                lines.append("")

        if self.action_items:
            lines.append("## Action Items\n")
            lines.extend(f"- [ ] {item}" for item in self.action_items)

        return "\n".join(lines)


class MeetingResult(BaseModel):
    """Complete meeting processing result."""

    transcript: Transcript
    summary: Summary | None = None
    source_filename: str
