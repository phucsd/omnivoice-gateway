# OmniVoice Gateway API Documentation

OmniVoice Gateway is a secure, high-performance RESTful API and user gateway for speech synthesis, voice cloning, automatic speech recognition, and video dubbing. This document serves as a complete reference for bots, coding agents, and developers integrating OmniVoice.

---

## 1. Overview & General Info

* **Protocol**: HTTPS REST API
* **Base URL**: The gateway client resolves the backend URL dynamically. In local development it is typically `http://localhost:8000` or proxied to a Hugging Face Space backend (e.g., `https://voice.oloka.net`).
* **Request & Response Format**: JSON (UTF-8 encoding). Audio files are returned as binary downloads (WAV / MP4).

---

## 2. Authentication

All requests to secured endpoints must include your API Key in the `Authorization` header as a Bearer token:

```http
Authorization: Bearer YOUR_API_KEY
```

Some public endpoints (like reading the public voice library) do not require authentication. Some media preview links support authentication via a query parameter: `?token=YOUR_API_KEY`.

---

## 3. Text-to-Speech (TTS) & Voice Cloning

The TTS pipeline operates asynchronously. You create a job, poll its status, and download the resulting WAV audio once completed.

### 3.1 Create TTS Job
* **Method**: `POST`
* **Path**: `/v1/tts/jobs`
* **Content-Type**: `application/json`

#### Request Body Parameters
| Parameter | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `mode` | string | **Yes** | - | Speech synthesis mode: `clone_voice` (voice cloning), `auto_voice` (pre-defined speaker), or `voice_design` (generated voice). |
| `text` | string | **Yes** | - | The input text to synthesize (recommended max 500 chars). Supports emotional tags and phonetic overrides. |
| `voice_sample_id` | string | Optional | - | The voice ID (`vs_xxxx`) to use for `clone_voice` mode. |
| `ref_text` | string | Optional | - | The reference text corresponding to the voice sample's audio. If omitted, the worker automatically transcribes the sample with Whisper. |
| `speed` | float | Optional | `1.0` | Playback speed multiplier (range: `0.5` to `2.0`). |
| `num_step` | integer | Optional | `32` | Diffusion steps (neural network quality). Range: `16` to `64`. |
| `denoise` | boolean | Optional | `true` | Apply post-inference noise filtering. |
| `guidance_scale` | float | Optional | `2.0` | Classifier-free guidance scale. Higher values follow prompt attributes closely. |
| `t_shift` | float | Optional | `0.1` | Noise schedule time shift. |
| `position_temperature` | float | Optional | `5.0` | Position random temperature. |
| `class_temperature` | float | Optional | `0.0` | Class sample temperature (randomness level). `0.0` is deterministic. |
| `layer_penalty_factor` | float | Optional | `5.0` | Depth codebook penalty factor to boost initial stability. |
| `duration` | float | Optional | - | Fixed output audio length (in seconds). |
| `preprocess_prompt` | boolean | Optional | `true` | Clean and remove silence from the voice sample prompt. |
| `postprocess_output` | boolean | Optional | `true` | Clean and remove trailing silence from output audio. |
| `audio_chunk_duration` | float | Optional | `15.0` | Maximum length of single chunks when processing long text (in seconds). |
| `audio_chunk_threshold` | float | Optional | `30.0` | Text length threshold (in characters/seconds) to trigger multi-chunk division. |
| `with_alignment` | boolean | Optional | `false` | Return word-level timestamps (start and end times) in the job status. |
| `language` | string | Optional | - | Target language code (e.g. `vi`, `en`, `zh`, `ja`, `ko`, `fr`, `de`, `es`). |
| `pad_duration` | float | Optional | - | Additional silence padding duration (seconds). |
| `fade_duration` | float | Optional | - | Smooth fade in/out duration (seconds). |
| `normalize_text` | boolean | Optional | `true` | Automatically normalize numbers, dates, abbreviations, and currencies into natural spoken words. |

#### Response (200 OK)
```json
{
  "job_id": "job_1be7696c9157",
  "status": "queued",
  "message": "Job added to queue."
}
```

---

### 3.2 Check Job Status (Polling)
* **Method**: `GET`
* **Path**: `/v1/jobs/{job_id}`

#### Response (200 OK)
```json
{
  "job_id": "job_1be7696c9157",
  "status": "completed",
  "progress": 100,
  "audio_url": "/v1/tts/jobs/job_1be7696c9157/audio",
  "text": "Xin chào, đây là giọng đọc nhân bản.",
  "alignment": [
    { "word": "Xin", "start": 0.12, "end": 0.45 },
    { "word": "chào,", "start": 0.45, "end": 0.78 },
    { "word": "đây", "start": 0.78, "end": 0.98 },
    { "word": "lành", "start": 0.98, "end": 1.25 }
  ],
  "error_message": null,
  "created_at": "2026-07-21T02:00:00.000Z",
  "started_at": "2026-07-21T02:00:01.000Z",
  "completed_at": "2026-07-21T02:00:05.000Z",
  "processing_time": 4.0,
  "queue_time": 1.0,
  "total_time": 5.0
}
```

#### Status States
* `queued`: Job is waiting in the queue.
* `starting_worker`: The assigned GPU worker is initializing.
* `running`: The worker is actively generating audio.
* `completed`: Generation is successful.
* `failed`: Generation failed. The details are in `error_message`.

---

### 3.3 Download Resulting Audio
* **Method**: `GET`
* **Path**: `/v1/tts/jobs/{job_id}/audio`
* **Headers**: `Authorization: Bearer YOUR_API_KEY` (Required!)

Returns a binary stream of the output audio (`audio/wav`).

---

## 4. Word-Level Alignment & Timing

If `with_alignment` was set to `true` during job creation, the successful job status response includes the `alignment` array.
* **Mốc thời gian**: Given as decimal seconds relative to the audio start (`0.0`).
* **Karaoke / Subtitles**: Can be used to sync voiceover words perfectly with on-screen text.

---

## 5. Fine-Grained Speech Controls

You can insert tags directly into the input `text` parameter to manipulate expressions or fix pronunciation.

### 5.1 Non-Verbal Expressions
Inject human sound cues by placing brackets around tags at any point in the text:
* `[laughter]`: Laughter
* `[sigh]`: A sigh of relief or fatigue
* `[sniff]`: Sniffing sound
* `[question-en]`, `[question-ah]`: Confused/inquisitive tone adjustments
* `[surprise-ah]`, `[surprise-oh]`: Surprised reactions

*Example*: `"[laughter] Thật tuyệt vời! [sigh] Nhưng tôi hơi mệt rồi."`

### 5.2 Phonetic Pronunciation Correction (Phonetic Overrides)
Correct spelling overrides when terms, abbreviations, or names are mispronounced:
* **English Phonetics**: Standard CMU capitalization inside brackets:
  * `"read as [B EY1 S]"` (forces pronunciation matching the word "base").
* **Chinese Pinyin & Tones**: Capitalized Pinyin syllables followed by tone numbers (1-4) inside brackets:
  * `"打[ZHE2]出售"` (forces folding tone 2 for 折).

### 5.3 Text Normalization (Đọc chuẩn số & ký tự)
Khi `normalize_text` được đặt thành `true` (mặc định: `true`), engine tự động chuyển hóa:
* Các con số và số thứ tự: `123` -> `"một trăm hai mươi ba"`.
* Ngày tháng, năm: `20/11/2024` -> `"ngày hai mươi tháng mười một năm hai nghìn không trăm hai mươi tư"`.
* Tiền tệ và đơn vị: `50.000đ`, `$100` -> `"năm mươi nghìn đồng"`, `"một trăm đô la"`.
* Tỷ lệ phần trăm và đơn vị đo lường: `99%`, `50km/h` -> `"chín mươi chín phần trăm"`, `"năm mươi ki-lô-mét trên giờ"`.

### 5.4 Engine Performance & GPU Acceleration
* **PyTorch Native SDPA Attention**: Tối ưu hóa bộ xử lý GPU CUDA với cơ chế Scaled Dot-Product Attention tích hợp sẵn, giúp sinh giọng nói với tốc độ cao, ổn định tuyệt đối và độ trễ cực thấp.
* **VoiceClonePrompt Caching**: Tự động trích xuất và lưu trữ bộ nhớ đệm đặc trưng âm thanh mẫu (`VoiceClonePrompt`) trong các tác vụ lồng tiếng video (dubbing) và sinh audio đa đoạn, giảm hơn 70% thời gian xử lý toàn trình.

---

## 6. Voice Library & Management

### 6.1 List Public Voice Samples (No Auth Required)
* **Method**: `GET`
* **Path**: `/v1/voice-library`
* **Query Parameters**:
  * `tag` (string, optional): Filter by tags (e.g. `Miền Nam`, `Trẻ`, `Kể chuyện`).
  * `search` (string, optional): Text query to search voice names.
  * `limit` (int, default 50): Page size.
  * `offset` (int, default 0): Pagination offset.

### 6.2 Update Voice Sample Details
* **Method**: `PUT`
* **Path**: `/v1/voice-samples/{voice_sample_id}`
* **Payload Format** (JSON):
  ```json
  {
    "name": "Giọng Nam Bộ Trẻ Đọc Truyện",
    "tags": ["Miền Nam", "Trẻ", "Kể chuyện"],
    "ref_text": "Văn bản tham chiếu mới",
    "is_public": true
  }
  ```

---

## 7. ElevenLabs Compatibility API

You can use standard ElevenLabs SDKs/clients by pointing their base URL to the OmniVoice Gateway endpoint.

### 7.1 List ElevenLabs-compatible Voices
* **Method**: `GET`
* **Path**: `/v1/voices`

Returns a list matching ElevenLabs JSON schema containing combined public and private voice lists.

### 7.2 Get Voice Audio Preview
* **Method**: `GET`
* **Path**: `/v1/voices/{voice_id}/previews`
* **Authentication**: Optional for public voices; for private voices pass `Authorization: Bearer YOUR_API_KEY` or `xi-api-key: YOUR_API_KEY` (and create a Blob URL in browsers rather than exposing API keys in query strings).

### 7.3 ElevenLabs Text-to-Speech Compatibility
* **Method**: `POST`
* **Path**: `/v1/text-to-speech/{voice_id}`
* **Headers**: `xi-api-key: YOUR_API_KEY` or `Authorization: Bearer YOUR_API_KEY`
* **Payload** (JSON): `{"text": "Xin chào!", "voice_settings": {"speed": 1.0}}`

---

## 8. Speech-to-Text (ASR)

Convert uploaded audio files into transcripts.

### 8.1 Create ASR Job
* **Method**: `POST`
* **Path**: `/v1/asr`
* **Content-Type**: `multipart/form-data`
* **Payload**:
  * `file` (Binary file, e.g. WAV, MP3, M4A)

Returns a standard job response containing a `job_id` and initial state.

### 8.2 Poll ASR Job Progress
* **Method**: `GET`
* **Path**: `/v1/jobs/{job_id}`

Upon completion (`status` is `completed`), the transcribed text is available in the `text` attribute of the response.

### 8.3 Get Original ASR Audio
* **Method**: `GET`
* **Path**: `/v1/asr/jobs/{job_id}/audio`

Retrieves the raw audio upload associated with this job.

---

## 9. Video Dubbing API

A multi-stage asynchronous processing pipe to download a video, split audio into dialog vocals and background music (BGM), translate & transcribe segments, generate cloned voices for each speaker, and compile a finalized video.

### 9.1 Upload Local Video File
* **Method**: `POST`
* **Path**: `/v1/video-dubbing/upload`
* **Content-Type**: `multipart/form-data`
* **Payload**:
  * `file`: Binary video file (MP4, MKV, etc.)

Returns a `VideoDubbingJobResponse` containing the `job_id`.

### 9.2 Create Video Dubbing Job (YouTube or Pre-Uploaded Video)
* **Method**: `POST`
* **Path**: `/v1/video-dubbing`
* **Content-Type**: `multipart/form-data`
* **Form Fields**:
  * `youtube_url` (string, optional): Public YouTube video URL (if not using `file` or `existing_job_id`).
  * `file` (binary, optional): Direct video upload if not pre-uploaded via `/v1/video-dubbing/upload`.
  * `existing_job_id` (string, optional): `job_id` returned from `/v1/video-dubbing/upload`.
  * `target_language` (string, default `"Vietnamese"`): Target translation language (e.g. `"Vietnamese"`, `"English"`, `"Japanese"`).
  * `voice_mode` (string, default `"auto_clone"`): `"auto_clone"` (extract speaker stem from video) or `"custom"` (use `voice_sample_id`).
  * `voice_sample_id` (string, optional): Voice ID when `voice_mode="custom"`.
  * `enable_separation` (boolean, default `true`): Separate vocals and BGM stems before ASR/mixing.
  * `video_quality` (string, default `"1080p"`): Preferred YouTube download resolution (`"360p"`, `"480p"`, `"720p"`, `"1080p"`, `"1440p"`, `"2160p"`).
  * `llm_profile_id` (string, optional): Override active LLM profile for translation.

---

### 9.3 Get Dubbing Job Status & Subtitles
* **Method**: `GET`
* **Path**: `/v1/video-dubbing/jobs/{job_id}`

Returns the full state machine details including `original_subtitles` and `translated_subtitles`:
```json
{
  "job_id": "vd_xxxx",
  "status": "awaiting_review",
  "progress": 60,
  "youtube_url": "https://www.youtube.com/watch?v=xxxxxx",
  "target_language": "Vietnamese",
  "voice_mode": "auto_clone",
  "enable_separation": true,
  "original_subtitles": [
    {
      "id": 1,
      "start": 1.25,
      "end": 4.50,
      "text": "Hello, welcome to this video.",
      "speaker": "SPEAKER_00"
    }
  ],
  "translated_subtitles": [
    {
      "id": 1,
      "start": 1.25,
      "end": 4.50,
      "text": "Xin chào, chào mừng bạn đến với video này.",
      "speaker": "SPEAKER_00",
      "voice_sample_id": null,
      "speed": 1.0
    }
  ]
}
```

#### Dubbing Pipeline Status States
1. `uploaded`: Local video uploaded as draft, waiting to start pipeline.
2. `queued` / `downloading`: Initializing or downloading source video.
3. `separating_audio`: Splitting original vocals from BGM stem.
4. `transcribing`: Running Whisper ASR & speaker segment alignment.
5. `translating`: Translating source segments to `target_language` via LLM.
6. `awaiting_review`: Ready for subtitle review & editing before TTS rendering.
7. `generating_tts`: Synthesizing dubbed voice segments on GPU worker.
8. `mixing_audio`: Mixing dubbed speech with BGM and residual vocals.
9. `muxing_video`: Encoding final playable MP4 output.
10. `completed` / `failed` / `cancelled`: Terminal states.

---

### 9.4 Update Subtitle Segments
Before finalizing (while in `awaiting_review`), update `original_subtitles` and `translated_subtitles`:
* **Method**: `PUT`
* **Path**: `/v1/video-dubbing/jobs/{job_id}/subtitles`
* **Payload**:
  ```json
  {
    "original_subtitles": [
      {
        "id": 1,
        "start": 1.25,
        "end": 4.80,
        "text": "Hello, welcome to this video.",
        "speaker": "SPEAKER_00"
      }
    ],
    "translated_subtitles": [
      {
        "id": 1,
        "start": 1.25,
        "end": 4.80,
        "text": "Xin chào, rất vui được gặp các bạn!",
        "speaker": "SPEAKER_00",
        "voice_sample_id": "vs_southern_male",
        "speed": 1.0
      }
    ]
  }
  ```

---

### 9.5 Finalize Dubbing (Trigger Rendering)
* **Method**: `POST`
* **Path**: `/v1/video-dubbing/jobs/{job_id}/finalize`
* **Payload** (JSON, optional): `{"bgm_volume": 0.6, "vocals_volume": 0.15}`

Triggers the backend worker to synthesize dubbed segments, mix stems, and mux the final MP4 (`generating_tts` -> `mixing_audio` -> `muxing_video` -> `completed`). You can cancel an active dubbing job via `POST /v1/video-dubbing/jobs/{job_id}/cancel`.

---

### 9.6 Download Pipeline Assets
* **Original Video**: `GET /v1/video-dubbing/jobs/{job_id}/video`
* **Extracted Vocals WAV**: `GET /v1/video-dubbing/jobs/{job_id}/vocals`
* **Extracted BGM WAV**: `GET /v1/video-dubbing/jobs/{job_id}/bgm`
* **Final Dubbed MP4 Video**: `GET /v1/video-dubbing/jobs/{job_id}/output`
* **Real-time Pipeline Logs**: `GET /v1/video-dubbing/jobs/{job_id}/log`

---

## 10. Code Integration Examples

### 10.1 cURL
```bash
# 1. Create TTS Job
curl -X POST "https://voice.oloka.net/v1/tts/jobs" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "clone_voice",
    "voice_sample_id": "vs_xxxx",
    "ref_text": "Văn bản mẫu giọng gốc",
    "text": "Xin chào, đây là cURL request.",
    "speed": 1.0,
    "num_step": 32,
    "with_alignment": true,
    "normalize_text": true
  }'

# 2. Get Job Status (Repeat every 3-5 seconds)
curl -X GET "https://voice.oloka.net/v1/jobs/YOUR_JOB_ID" \
  -H "Authorization: Bearer YOUR_API_KEY"

# 3. Download WAV Audio File
curl -o output.wav -X GET "https://voice.oloka.net/v1/tts/jobs/YOUR_JOB_ID/audio" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### 10.2 Python
```python
import time
import requests

API_URL = "https://voice.oloka.net"
API_KEY = "YOUR_API_KEY"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

payload = {
    "mode": "clone_voice",
    "voice_sample_id": "vs_xxxx",
    "text": "Xin chào, đây là tích hợp Python.",
    "with_alignment": True,
    "normalize_text": True
}

# 1. Submit request
response = requests.post(f"{API_URL}/v1/tts/jobs", json=payload, headers=headers)
response.raise_for_status()
job_id = response.json()["job_id"]
print(f"Job created: {job_id}")

# 2. Poll progress
while True:
    status_res = requests.get(f"{API_URL}/v1/jobs/{job_id}", headers=headers)
    status_res.raise_for_status()
    job_data = status_res.json()
    status = job_data["status"]
    print(f"Status: {status} • Progress: {job_data['progress']}%")
    
    if status == "completed":
        break
    elif status == "failed":
        raise Exception(f"Job failed: {job_data['error_message']}")
    time.sleep(3)

# 3. Download output WAV file
audio_res = requests.get(f"{API_URL}/v1/tts/jobs/{job_id}/audio", headers=headers)
audio_res.raise_for_status()
with open("output.wav", "wb") as f:
    f.write(audio_res.content)
print("File downloaded as output.wav")
```

### 10.3 NodeJS
```javascript
const fs = require('fs'); // Node.js >= 18 includes global fetch

const API_URL = "https://voice.oloka.net";
const API_KEY = "YOUR_API_KEY";

const headers = {
  'Authorization': `Bearer ${API_KEY}`,
  'Content-Type': 'application/json'
};

async function execute() {
  // 1. Submit job
  const res = await fetch(`${API_URL}/v1/tts/jobs`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      mode: 'clone_voice',
      voice_sample_id: 'vs_xxxx',
      text: 'Xin chào từ NodeJS client.',
      with_alignment: true,
      normalize_text: true
    })
  });
  if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
  const { job_id } = await res.json();
  console.log(`Job created: ${job_id}`);

  // 2. Poll progress
  while (true) {
    const statusRes = await fetch(`${API_URL}/v1/jobs/${job_id}`, { headers });
    const jobData = await statusRes.json();
    console.log(`Status: ${jobData.status} (${jobData.progress}%)`);
    
    if (jobData.status === 'completed') {
      break;
    } else if (jobData.status === 'failed') {
      throw new Error(`Job failed: ${jobData.error_message}`);
    }
    await new Promise(r => setTimeout(r, 3000));
  }

  // 3. Download WAV Audio
  const audioRes = await fetch(`${API_URL}/v1/tts/jobs/${job_id}/audio`, { headers });
  if (!audioRes.ok) throw new Error(`Audio HTTP error! status: ${audioRes.status}`);
  const buffer = Buffer.from(await audioRes.arrayBuffer());
  fs.writeFileSync('output.wav', buffer);
  console.log('Audio file saved to output.wav');
}

execute().catch(console.error);
```
