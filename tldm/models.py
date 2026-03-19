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


class Note(BaseModel):
    """A single finding with optional reasoning and quote."""

    finding: str
    reasoning: str = ""
    quote: str = ""


class Section(BaseModel):
    """A topic section containing multiple notes."""

    topic: str
    notes: list[Note]


class Summary(BaseModel):
    """Meeting summary with purpose-driven structure."""

    title: str
    excerpt: str
    key_points: list[str]
    action_items: list[str]
    participants: list[str] = []
    notes: list[Section] = []

    def to_markdown(self) -> str:
        """Format summary as markdown.

        Returns:
            Markdown-formatted summary string.
        """
        lines = [f"# {self.title}\n"]
        lines.append(f"{self.excerpt}\n")

        if self.participants:
            lines.append("## Participants\n")
            lines.extend(f"- {p}" for p in self.participants)
            lines.append("")

        lines.append("## Key Points\n")
        lines.extend(f"- {point}" for point in self.key_points)

        if self.action_items:
            lines.append("\n## Action Items\n")
            lines.extend(f"- [ ] {item}" for item in self.action_items)

        if self.notes:
            lines.append("\n## Notes\n")
            for section in self.notes:
                lines.append(f"### {section.topic}\n")
                for note in section.notes:
                    lines.append(f"- {note.finding}")
                    if note.reasoning:
                        lines.append(f"  - Why: {note.reasoning}")
                    if note.quote:
                        lines.append(f'  - > "{note.quote}"')
                lines.append("")

        return "\n".join(lines)


class MeetingResult(BaseModel):
    """Complete meeting processing result."""

    transcript: Transcript
    summary: Summary | None = None
    source_filename: str
