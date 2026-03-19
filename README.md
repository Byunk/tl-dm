# tl;dm - Too Long; Did Meet

Turn any video or audio into a transcript + summary in one command.
Works with local files and Google Drive. No $20/month subscription. No cloud lock-in. Knows who said what.

## Install

Requires [uv](https://docs.astral.sh/uv/) and [ffmpeg](https://ffmpeg.org/). [gcloud CLI](https://cloud.google.com/sdk/docs/install) is only needed for Google Drive features.

```bash
uv tool install git+https://github.com/Byunk/tl-dm.git
```

For development:

```bash
git clone https://github.com/Byunk/tl-dm.git && cd tl-dm
uv sync
```

### LLM API Key

```bash
export GEMINI_API_KEY="your-key"
# Or for other providers:
# export OPENROUTER_API_KEY="your-key"
# export OPENAI_API_KEY="your-key"
# export ANTHROPIC_API_KEY="your-key"
```

### Google Drive (optional)

Only needed if you want to process files from Google Drive or upload results back to Drive.

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

## Usage

```bash
# Local video file
tldm transcribe recording.mp4
tldm summarize ~/Downloads/meeting.mp4

# Local audio file (skips ffmpeg extraction)
tldm transcribe interview.mp3

# Google Drive
tldm summarize <drive-url-or-file-id>

# Upload results to the same Drive folder
tldm summarize <drive-url-or-file-id> --upload

# Use a different model
tldm summarize recording.mp4 --model gemini/gemini-2.5-flash
tldm summarize <drive-url> --summary-model openrouter/anthropic/claude-sonnet-4
```

## Configuration

Settings are loaded in order: constructor > env vars > defaults.

| Setting | Default | Env var |
|---------|---------|---------|
| Transcription model | `gemini/gemini-3.1-flash-lite-preview` | `TLDM_TRANSCRIPTION_MODEL` |
| Summary model | `gemini/gemini-3.1-flash-lite-preview` | `TLDM_SUMMARY_MODEL` |
| Service account path | (none) | `TLDM_SERVICE_ACCOUNT_PATH` |

## Supported Models

Any model available through [LiteLLM](https://docs.litellm.ai/docs/providers) works.

Note: Transcription requires a model that supports audio input (currently Gemini models).
