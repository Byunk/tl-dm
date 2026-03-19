# tl;dm - Too Long; Did Meet

One command. Full transcript with speaker names. Decision-ready summary. From any meeting recording.

No $20/month subscription. No cloud lock-in. Runs on your machine.

## See it in action

Input: [Ilya Sutskever on Dwarkesh Patel](https://www.youtube.com/watch?v=aR20FWCCjAs) (1h 36m podcast)

```bash
tldm summarize ilya_dwarkesh.mp3 --context "Podcast interview about the future of AI"
```

Output ([full summary](examples/ilya_dwarkesh_summary.md) | [full transcript](examples/ilya_dwarkesh_transcript.md)):

> **Transitioning to the Age of Research: Ilya Sutskever on the Path to Safe Superintelligence**
>
> Ilya Sutskever discusses the limitations of current AI scaling, the founding philosophy
> of SSI, and the necessity of aligning AI with sentient life. The conversation explores
> why current models lack real-world economic impact despite high benchmark scores.
>
> **Participants**
> - Ilya Sutskever — Co-founder and Chief Scientist of SSI, formerly at OpenAI
> - Dwarkesh Patel — Host of the Dwarkesh Podcast
>
> **Key Points**
> - Significant disconnect between benchmark performance and actual economic impact
> - The "Age of Scaling" is shifting back to an "Age of Research" as novel ideas deplete
> - RL scaling follows a sigmoid curve, not the power law seen in pre-training
> - Human learning is far more sample-efficient than AI, suggesting undiscovered ML principles
> - SSI adopts a "straight shot" strategy, avoiding the market rat race of intermediate releases
> - Alignment may work better focused on "sentient life" broadly rather than human life specifically
> - Research taste — top-down intuition for simplicity — sustains breakthroughs through failure
> - Timeline for human-like learner that becomes superhuman: 5 to 20 years

Identified speakers by name. Extracted key decisions. Ready to share — in under 5 minutes.

## Quick Start

```bash
uv tool install git+https://github.com/Byunk/tl-dm.git
export GEMINI_API_KEY="your-key"
tldm summarize recording.mp4
```

Requires [uv](https://docs.astral.sh/uv/) and [ffmpeg](https://ffmpeg.org/).

## Usage

```bash
# Local video or audio
tldm summarize meeting.mp4
tldm transcribe interview.mp3

# Google Drive — download, process, and upload results back
tldm summarize <drive-url> --upload

# Add context for better summaries
tldm summarize standup.mp4 --context "Daily standup, focus on blockers"

# Mix and match models
tldm summarize recording.mp4 --model gemini/gemini-2.5-flash
tldm summarize recording.mp4 --summary-model openrouter/anthropic/claude-sonnet-4
```

## Configuration

| Setting | Default | Env var |
|---------|---------|---------|
| Transcription model | `gemini/gemini-3.1-flash-lite-preview` | `TLDM_TRANSCRIPTION_MODEL` |
| Summary model | `gemini/gemini-3.1-flash-lite-preview` | `TLDM_SUMMARY_MODEL` |
| Service account path | (none) | `TLDM_SERVICE_ACCOUNT_PATH` |

Any model available through [LiteLLM](https://docs.litellm.ai/docs/providers) works. Transcription requires a model that supports audio input (currently Gemini models).

<details>
<summary>Google Drive setup (optional)</summary>

Only needed if you want to process files from Google Drive or upload results back to Drive. Requires [gcloud CLI](https://cloud.google.com/sdk/docs/install).

Each user must set up their own GCP project (Google does not allow unverified apps to be used by arbitrary users).

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Enable the **Google Drive API**:
   - **APIs & Services** > **Library** > search "Google Drive API" > **Enable**
4. Configure the OAuth consent screen:
   - **APIs & Services** > **OAuth consent screen**
   - User type: **External** > **Create**
   - Fill in app name and your email
   - Go to **Audience** > **Add users** > add your own email as a test user
5. Create OAuth credentials:
   - **APIs & Services** > **Credentials**
   - **Create Credentials** > **OAuth client ID** > Application type: **Desktop app**
   - Click **Create** and **Download JSON**
6. Save the downloaded JSON:
   ```bash
   mkdir -p ~/.config/tldm
   mv ~/Downloads/client_secret_*.json ~/.config/tldm/credentials.json
   ```
7. Authenticate (one-time, opens browser):
   ```bash
   gcloud auth application-default login \
     --client-id-file="$HOME/.config/tldm/credentials.json" \
     --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive
   ```

</details>

## Development

```bash
git clone https://github.com/Byunk/tl-dm.git && cd tl-dm
uv sync
```
