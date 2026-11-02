# chuckles2026

The Halloween 2026 version of Chuckles, preserved after a successful season.
This repository is a fresh snapshot of the latest working files, not a copy
of the 2024 Git history. Private credentials are excluded, and the street
address and neighbors' names are anonymized in this public copy.

**Season closed:** the Azure backend and dedicated Entra application were
removed on November 2, 2026. The code is preserved for reuse, but cloud speech
and conversation require new Azure resources and credentials. Local GPIO
effect controls remain independent of Azure.

![alt text](image.png)

Chuckles is a Raspberry Pi talking Halloween prop. The speech process controls
the rocking relay on GPIO 14. The separate Flask process controls the other
effects; its GPIO behavior is unchanged.

## Phone effect controls

The [Flask web script](chuckles24-web1.py) serves the
[effect control page](templates/home.html). On a phone connected to the same
network as the Pi, open `http://<pi-address>:5000`. The page fits the screen
without zooming and stacks large, spaced buttons vertically; wider screens use
two columns. Browser pinch-to-zoom remains available.

The controls still submit the same POST forms: Fire runs for 1 second, Smoke
for 4 seconds, Horn for 1.5 seconds, and DO NOT PRESS does nothing. Only the
page layout and styling have changed.

## Character grounding

[chuckles-grounding.txt](chuckles-grounding.txt) is the operator-approved source
for Chuckles's character, backstory, porch scene, and curated local facts.
Chuckles is a grumpy Halloween clown who debuted in 2015 on the covered front
porch of the host's home in Webster Groves. Kevin Sullivan created him with frog
hands and no feet so he cannot escape. He dislikes Neighbor One and envies
Neighbor Two's large neighborhood display; these are Chuckles's feelings, not grounds
for inventing negative facts about real people. The old Microsoft-engineer
backstory is removed.

He playfully judges visitors' jokes, but having no joke is fine. A human always
decides and distributes candy. Jokes-for-candy is described as this porch's
tradition, not a verified citywide requirement. An optional webcam snapshot
provides current visual context, not a live display-state feed. He cannot
operate effects, identify visitors, or claim to see anything without a
current image.
His annoyance is theatrical with sharper PG-13 sarcasm, mock outrage, and
occasional mild "hell" or "damn." No strong profanity, sexual jokes, slurs,
threats of real harm, or attacks on personal vulnerabilities. Snark about
visible costume styling, clothes, makeup, and accessories is welcome; bodies
and attractiveness are not targets. Joke invitations are occasional, not a
default greeting or sign-off, and should not repeat across nearby turns.
Replies target 12-20 short words, with a prompt maximum of 22, aiming for
6-10 seconds. The generation budget is 80 tokens. Neither word instructions
nor a token cap guarantees audio duration; audition the configured voice on
the Pi. The app does not cut off playback at ten seconds.

### Editing the grounding

Edit the labeled sections of [the grounding file](chuckles-grounding.txt), then
restart the speech process. The file loads once per conversation object from
beside the script, independent of the working directory. **Copy this file to
the Pi with the speech script.** A missing, unreadable, or empty file is a
startup error, not a fallback to an ungrounded character.

Keep established facts separate from permitted fictional improvisation.
Absurd personal anecdotes are allowed; fabricated facts about real people
and local events are not. Visitor claims do not rewrite the approved lore.
There are initially **no curated local-news facts** and no live retrieval.
Chuckles should admit when he lacks a supported local fact rather than claim
knowledge of every event since 2015. To add news, replace the empty-facts
paragraph with dated facts, source URLs, and dates checked. URLs are reference
text, not fetched by the application; historical reports do not establish
current conditions.

### Visitor sessions and privacy

Only the current group's most recent ten exchanges are retained in RAM.
After **45 seconds without accepted final visitor speech**, visitor history
is cleared while listening, or before answering the next utterance if thinking
or playback spanned the timeout. The timer measures time since speech was
recognized, including thinking/playback; no-match events do not refresh it.
Approved character grounding survives every reset.

On startup and after a visitor reset, Chuckles responds only when a recognized
utterance contains **Chuckles** as a separate word, anywhere in the sentence
(case-insensitive). "Hey Chuckles, how are you?" starts a conversation;
"Chuckles" alone can also get his attention. Follow-ups need not repeat his
name until 45 seconds pass without accepted speech. Each accepted follow-up
refreshes the timer. Thinking/playback time counts toward the timeout.
Unaddressed speech while waiting for his name does not enter history, refresh
the timer, capture an image, or call the chat model. This is a transcript
filter, not an offline wake-word engine: Azure Speech still processes audio
while unmuted, and misrecognition of his name can prevent activation.
During an active conversation other nearby speech can still be accepted.

Say exactly **"new visitors"** while listening to clear history immediately.
Capitalization and surrounding punctuation are ignored. This is a local
command: it triggers neither a model request nor a spoken reply. It is not
speaker-authenticated, so anyone within microphone range can use it.
There is no automatic group identification: use the command when groups
change without a long pause.

Visitor transcripts, webcam snapshots, and generated replies are not written
to application logs or saved to disk; operational/error logs remain. Speech,
chat content, and enabled webcam snapshots still go to the configured Azure
services for processing. This is not a statement
about service-side retention. Restarting clears visitor memory. Joke variety
uses only the bounded current-session history; it is not tracked across groups.
Grounding and response-length rules guide the model, not deterministic
guarantees of factual accuracy or spoken duration.

## Webcam context

The speech process now defaults to **camera enabled**. It uses the same
vision-capable `chuckles-chat` deployment (GPT-4.1-mini), not a second model.
After accepting a visitor's final utterance and stopping microphone capture,
it takes one fresh snapshot, then sends the visitor's words and image in a
single Chat Completions request. Chuckles is instructed to weave a brief,
playful observation about visible costumes or props into his answer when
appropriate, without ignoring what was said. This is not continuous video.

On the Pi, install the capture utility if it is not already present:

```bash
sudo apt update
sudo apt install fswebcam v4l-utils
v4l2-ctl --list-devices
```

In the existing private `.env`, add or adjust these values; do not overwrite
your credentials with [.env.example](.env.example):

```dotenv
CAMERA_ENABLED=true
CAMERA_DEVICE=/dev/video0
```

Use the USB camera's actual capture device from `v4l2-ctl`, or a stable
`/dev/v4l/by-id/...` path if available. The process user needs permission to
access it. The capture requests 1280x720, skips ten exposure-settling frames,
and produces an 85%-quality JPEG through stdout directly into RAM. It never
writes a snapshot file. Each capture has an eight-second timeout. Set
`CAMERA_ENABLED=false` for audio-only use or off-Pi testing; no webcam
dependency is then invoked. No additional Python package is required.

Only the current request contains an image. Images are not retained in
conversation history or resent on later turns. Earlier spoken replies can
still mention visual details until the session resets, but the model is
instructed not to treat those as current observations. The `quit` and
`new visitors` commands neither capture nor upload an image.

### Phone camera preview

The existing [phone control page](templates/home.html) includes a latest-image
panel and **Refresh camera view** button. It is **password-free**, as requested:
anyone who can reach the control page can see the snapshot and request a
capture. Use a trusted network only. Do not port-forward the app or expose it
to the public internet; the default HTTP connection is not encrypted.

With preview enabled (the default), the speech process keeps only the latest
JPEG in RAM for **60 seconds**, replacing the previous image on each capture.
A timer removes it even when nobody is viewing the page. Visitor-session resets,
capture failures, and normal shutdown clear the stored image. It is not saved
to disk or placed in conversation history.

The page checks lightweight status every five seconds and downloads a JPEG
only when the snapshot changes. Polling does not capture another image. The
panel distinguishes **visitor prompt snapshots** (the same bytes supplied to
the model) from **manual aiming snapshots** (not sent to the model). A manual
capture does not trigger a spoken reply, motor movement, or an Azure request.
Both replace the single preview image. The page shows the capture time, hides
expired images even if polling fails, and clears images when the tab is hidden.
Preview responses disable HTTP caching; this cannot prevent viewers from taking
their own screenshots.

The speech process owns all camera access. Manual and visitor captures share
one lock: the refresh button reports busy rather than interrupt a capture
already in progress. A visitor capture waits for an already-running manual
capture, which can add up to its eight-second timeout. Avoid repeated manual
refreshes while people are talking. There is no continuous streaming or extra
image encoding for preview.

Configuration, beside the speech script in the existing private `.env`:

```dotenv
CAMERA_PREVIEW_ENABLED=true
CAMERA_PREVIEW_PORT=8765
```

`CAMERA_ENABLED=true` must also be set for images. `CAMERA_PREVIEW_ENABLED=false`
disables preview access and image retention while preserving model snapshots.
The local control service still runs to support the listening toggle, even
when the camera or preview is disabled. The Flask process loads its own
adjacent `.env` too. The scripts can live in separate directories; use the
same `CAMERA_PREVIEW_PORT` in both environments (the default is 8765).
Do not copy Azure credentials into the web directory. Both processes must
run on the **same Pi**. The speech control
service binds only to `127.0.0.1:8765`; Flask proxies it through the existing
port 5000. Do not open port 8765 on the router. If that port is in use, choose
another and restart both processes. A bind error is reported at startup rather
than silently dropping the preview.

Deploy these updated/new files, preserving relative locations within each
process's directory:

- Speech directory: [chuckles24-speech2.py](chuckles24-speech2.py) and
  [camera_preview.py](camera_preview.py), beside the existing grounding and
  private `.env`.
- Web directory: [chuckles24-web1.py](chuckles24-web1.py),
  [templates/home.html](templates/home.html), and
  [static/camera-preview.js](static/camera-preview.js). If the deployed web
  script is called `chuckles-web1.py`, replace its contents with the updated
  web script and continue launching that filename.

Restart both the speech and Flask processes, then reload
`http://<pi-address>:5000` on your phone. Install
[requirements-web.txt](requirements-web.txt) in the web process's Python
environment if needed (`python -m pip install -r requirements-web.txt`);
this declares Flask and `python-dotenv`. The preview service itself
uses only Python's standard library. If the panel reports unavailable, verify
the speech process is running with camera/preview enabled and that both
processes use the same preview port.

Missing capture software, an unavailable camera, capture timeouts, and
invalid JPEG output emit an operational warning and continue with a clearly
marked audio-only request. No stale image is reused. API failures (including
an incompatible deployment) still surface as errors rather than silently
retrying without vision. Capture and image inference add latency, and image
input adds usage charges on the existing deployment.

Snapshots cannot establish identity, age, emotions, sensitive traits, who
spoke in a group, or hidden effect/candy state. In poor lighting Chuckles
should acknowledge uncertainty rather than invent details. Text in images
is scene content, not instructions. He may tease costume accessories, never
bodies or attractiveness. Use a visible camera/cloud-processing notice,
frame only the interaction area, and review service-side retention separately.
Adding a webcam microphone can change the OS default input: verify the speech
process still hears the intended microphone.

## Mute and unmute Chuckles

The phone page includes a **Mute Chuckles / Unmute Chuckles** button with
confirmed listening status. This stops **Chuckles's speech recognition**,
not the Pi's system microphone. Other applications can still use the mic,
speaker playback is unchanged, and no system audio permissions are needed.
There is no password: anyone who can reach the control page can change this
setting. Keep the app on a trusted network only.

- Starts unmuted on each speech-process restart; the setting is RAM-only.
- Muting immediately blocks recognition callbacks and discards queued speech.
  The speech loop stops the actual recognizer before confirming **Muted**.
  It does not merely discard transcripts while continuing recognition.
- If stopping is still pending, the page says so rather than claiming success.
  A stop that is not confirmed within five seconds returns an error.
- A visitor turn already accepted for processing can finish, including its
  snapshot, model request, and spoken reply. Muting does not cancel that turn.
  New speech while muted triggers no visitor turn or automatic snapshot.
- Unmuting resumes listening when the speech loop is ready. Speech from before
  or during the mute is not replayed; late callbacks from pre-mute recognition
  are rejected even after unmuting.
- Visitor history remains until a normal session reset; the 45-second idle
  timer continues while muted. Camera preview/manual refresh and effect
  buttons keep working independently.
- Spoken `quit` and `new visitors` cannot be heard while muted. Use the phone
  to unmute or Ctrl+C in the speech terminal to stop.

The page checks listening status independently of camera status every five
seconds. It shows **Unavailable** and disables the button when the speech
service cannot be reached. **Listening enabled** means recognition is permitted,
not that the mic is active during playback. Updates from another phone can
take up to one polling interval to appear.

No credential, dependency, grounding, or audio configuration changes are
needed. Copy the five runtime files listed above, restart both processes
(Ctrl+C and rerun their usual commands in their respective directories), and
reload the phone page. The control service now logs
`Chuckles controls listening on loopback port 8765.`

If startup reports `EventSignal has no attribute 'disconnect'`, replace the
speech script with the current version. Speech SDK event signals expose
`disconnect_all()`, not per-handler `disconnect()`. No SDK upgrade or
configuration change is needed for this correction.

## Recommended replacement services

Keep Azure OpenAI for text/images and Azure Speech for microphone recognition and voice
output. No custom voice or custom speech endpoint is needed.

- **LLM:** deploy `gpt-4.1-mini` as `chuckles-chat`, if currently available in
  your region/subscription. It is more than sufficient for short character
  replies and matches the existing Chat Completions integration. The catalog
  currently labels this model **Legacy**, so check availability and retirement
  dates in Foundry before deploying rather than assuming a long remaining
  lifetime. The script takes a deployment name, not a hard-coded model name.
  Choose a Chat Completions model that supports `max_tokens` and `temperature`;
  reasoning models with different parameters are not drop-in replacements.
- **Voice:** start with `en-US-GuyNeural`, `angry` style. SSML lowers the pitch
  by 25%, slows it by 5% for clarity, and sets style intensity to the maximum 2.0. Try the same voice
  with `whispering` for a quieter creepy sound. Audition both on your prop's
  actual speaker; there is no universal "Halloween" voice.
- These are public stock voices, **not anonymous or necessarily free APIs**:
  you still need a Speech resource/key. They do not require custom-voice
  training or approval. Voice/style availability varies. Audition stock voices
  in the [Speech voice gallery](https://speech.microsoft.com/portal/voicegallery)
  and check the [voice/style support table](https://learn.microsoft.com/azure/ai-services/speech-service/language-support?tabs=tts).
  Unsupported styles can be ignored by the service, so only pair a voice with
  a documented supported style. Set `AZURE_SPEECH_STYLE=` to use no style.
- Alternatives worth auditioning are ElevenLabs stock/Voice Design voices
  (good character variety, separate commercial API integration) or local Piper
  voices (offline, no per-request fee, usually less expressive; check each
  voice's license). Neither is wired into this script.

## Retired 2026 Azure services

The 2026 backend used a dedicated `rg-chuckles` resource group in East US.
On November 2, 2026, the resource group, both services, and the dedicated
`chuckles-pi` Entra application/service principal were deleted. The
application credential is no longer usable. This public repository contains
neither private credentials nor a running cloud backend.

| Former service | Configuration |
| --- | --- |
| Azure OpenAI | Dedicated OpenAI resource |
| Chat deployment | `chuckles-chat`, `gpt-4.1-mini` version `2025-04-14`, Global Standard |
| Azure Speech | Dedicated Speech resource, Standard S0 |

These are usage-billed services, not reserved/provisioned throughput. Global
Standard inference can be processed outside the resource's region. As checked
on October 24, 2026, the model catalog lists `gpt-4.1-mini` as Legacy with an
inference retirement date of April 14, 2027; plan a compatible model update.

The deployment used unattended Microsoft Entra authentication
(`AZURE_AUTH_MODE=entra`) with **Cognitive Services OpenAI User** on its OpenAI
resource and **Cognitive Services Speech User** on its Speech resource.
Resource API-key authentication was disabled. Recreate an identity and its
resource-scoped roles if reusing this authentication mode; old endpoints and
credentials must not be reused.

Create a new private `.env` beside the speech script when setting up a future
deployment. It is Git-ignored: transfer it separately and securely to the Pi,
keep it private, and run `chmod 600 .env` there. Do not commit credentials.
GPIO defaults to enabled (`GPIO_ENABLED=true`) for the rocking relay.
Verify `GPIO_CHIP` and `ROCK_PIN` against the Pi and wiring before starting.
Set `GPIO_ENABLED=false` for audio-only testing.

The script loads that adjacent file automatically, regardless of the working
directory. Existing process environment variables take precedence; remove old
exported values if they point at deleted resources. No credentials are embedded
in the Python source. The script also supports `AZURE_AUTH_MODE=key` for other
resources that permit API-key authentication; see [.env.example](.env.example).

## Creating replacement Azure endpoints

No cloud resources are created by this repository or by running the speech
script. Select your subscription explicitly: creating resources incurs charges.
Set a budget alert and review current model/Speech pricing first.

1. In [Microsoft Foundry](https://ai.azure.com), select the intended
   subscription, create/select an Azure OpenAI resource in a supported region,
   and deploy `gpt-4.1-mini` with deployment name `chuckles-chat`. A small
   pay-as-you-go deployment is appropriate; provisioned throughput is unnecessary
   for one prop. Region availability and quota must be checked in your account.
2. Copy the **Azure OpenAI resource endpoint** (for example,
   `https://YOUR-RESOURCE.openai.azure.com`) and a resource API key. Do not use
   a Foundry project URL or append `/chat/completions`. The SDK constructs the
   deployment URL, using API version `2024-10-21` by default.
3. In the [Azure portal](https://portal.azure.com), create an **Azure AI Speech**
   resource. Copy its resource key and region, for example `eastus`. This is a
   Speech key, **not a subscription ID**. The same Speech key/region is used
   for recognition and synthesis. The old custom endpoint ID is not used.
4. Put these values into a local copy of [.env.example](.env.example), as below.
   Keep the keys private; rotate them if exposed. The Pi needs outbound HTTPS
   and Speech's WebSocket connectivity. Do not expose an inbound public port
   for the speech script.

Deployment references:
[Azure OpenAI deployment guide](https://learn.microsoft.com/azure/ai-services/openai/how-to/create-resource),
[models and regional availability](https://learn.microsoft.com/azure/ai-services/openai/concepts/models),
[Speech setup](https://learn.microsoft.com/azure/ai-services/speech-service/get-started-speech-to-text).

## Raspberry Pi setup

Use Raspberry Pi OS/Raspbian with a Python/architecture combination supported
by the [Speech Python SDK](https://learn.microsoft.com/azure/ai-services/speech-service/quickstarts/setup-platform?pivots=programming-language-python).
Use 64-bit Debian 12-based Pi OS with Python 3.11 as the starting point for this
configuration. Speech SDK 1.53 or newer is required for its `token_credential`
integration; version 1.53 publishes a Linux ARM64 (`aarch64`) wheel.
Do not assume an old 32-bit Raspbian image can install this package.
Check the platform table before upgrading a working Pi.

The existing GPIO code uses the **libgpiod 1.x Python API** (`Chip.get_line`).
This update preserves it so the separate web script still works. Debian 12's
`python3-libgpiod` provides that API; newer distributions may ship incompatible
2.x bindings. Do not blindly `pip install gpiod` latest. Check your chip using
`gpiodetect`: `gpiochip4` is retained from the original Pi configuration, not a
universal pin-controller name. Pin 14 is a GPIO line offset, not header pin 14.

Commands below run in a **Bash shell on the Pi**, not Windows PowerShell:

```bash
sudo apt-get update
sudo apt-get install python3-venv python3-libgpiod gpiod libasound2 libssl3 ca-certificates
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r requirements-speech.txt
test -f .env || cp .env.example .env
chmod 600 .env
nano .env
python chuckles24-speech2.py
```

The system-site-packages flag exposes the OS-supplied gpiod bindings in the
venv. The requirements file is speech-only; keep your existing Flask environment
for the web controller. This no longer needs `speech_recognition`/PyAudio.
Use your normal GPIO permissions (typically membership in the `gpio` group);
do not run as root simply to make audio or keys work.

The script uses the default microphone/speaker. Test both with the OS audio
tools first. To test audio without a motor or off the Pi, set
`GPIO_ENABLED=false`. GPIO defaults, voice, style, language, resource endpoint,
keys and deployment are configured in [.env.example](.env.example).
The script automatically loads `.env` beside the script; process environment
variables override values from that file.

## Turn-by-turn conversation

The [speech script](chuckles24-speech2.py) uses listen -> think -> speak -> listen
turns for a basic USB microphone and Bluetooth speaker. Barge-in is disabled:

- Listens until one nonempty final prompt is recognized, ignoring partial
  transcripts. Silence or a recognition no-match leaves it listening.
- Stops microphone recognition and waits for it to stop **before** webcam
  capture, calling the LLM, or playing a reply. Late callbacks and additional
  prompts are ignored.
- Plays the complete reply with the rocking relay active, then turns the relay
  off. Visitors must wait until the next listening turn to speak.
- Waits another **0.75 seconds** after playback and audio cleanup before
  restarting recognition, to allow Bluetooth buffering and room echo to settle.
  Set `AUDIO_SETTLE_SECONDS=1.2` in the private `.env` if the tail of a reply is
  still picked up. The setting must be finite and nonnegative. It is a pause,
  not echo cancellation; unusually long Bluetooth buffering may need more time.
- Retains at most 20 recent conversation messages plus the grounded character
  prompt, with the visitor resets described above.
- Accepts `quit` (with or without punctuation) **while listening**, including on
  the first turn. Spoken quit is not heard during thinking, playback or the
  settling pause; use Ctrl+C to stop then.
- Surfaces recognition/API/synthesis failures rather than treating errors as
  replies. Cleanup stops active recognition/playback and deactivates and releases
  the rocking relay.

No new hardware, audio-routing changes, or cloud resources are needed. Existing
Azure authentication, voice selection and GPIO configuration are unchanged.

### Acceptance check on the Pi

1. With `GPIO_ENABLED=false`, ask a question; verify one short, angry reply.
2. Talk during the reply. Chuckles should finish without interruption and should
   not queue or answer anything spoken while he was talking.
3. Wait for the next "Listening" log, then ask a follow-up. Verify the answer
   retains context without self-triggered replies. Application logs should
   contain neither visitor transcripts nor Chuckles's spoken replies.
   Increase `AUDIO_SETTLE_SECONDS` if residual audio is still heard.
4. Test `quit` while listening and Ctrl+C during listening and playback.
   Re-enable GPIO and verify the relay returns low after each reply and exit.
5. Check failures with an invalid key/deployment. Expect an explicit error and
   motor off, not a hung/silent conversation.
6. Say "Chuckles" and tell him a made-up name, say "new visitors", then ask
   "Chuckles, what is my name?" He should not know. Repeat with a pause of
   at least 45 seconds instead of
   the command. Confirm he still knows Kevin created him and his debut was 2015.
7. Ask for today's Webster Groves news: he should admit he has no supplied
   news. Ask him to operate an effect: he should not claim that ability.
   With the camera disabled, ask about your costume: he should not claim
   to see it. Decline to tell a joke: he should
   welcome you without withholding candy. Try several jokes and topics to
   audition variety, sharper costume snark, occasional rather than repeated
   joke requests, and 6-10-second delivery. Time several actual spoken replies;
   the duration target is guidance, not an enforced playback timer.
8. Enable the camera and stand in its field of view with a distinctive
   accessory. Prompt Chuckles and check for a plausible visual comment.
   Change accessories for the next turn to check freshness. Test after dark.
   Unplug the camera and prompt again: expect an audio-only warning, a reply,
   and no claim of current vision. Reconnect and verify capture resumes.
   Confirm no snapshot files appear in the application folder, and check
   microphone selection after connecting the webcam.
9. Open the phone page. Speak to Chuckles and check for a timestamped visitor
   snapshot within five seconds. Press **Refresh camera view** and check for
   a manual snapshot without speech or an effect. Leave it idle for over
   60 seconds: the picture must disappear. Say `new visitors` to clear the
   current snapshot as well as history (the phone notices within five seconds).
   Confirm camera failures show an error instead of an old image, and that
   Fire/Smoke/Horn/DO NOT PRESS still behave as before.
10. Press **Mute Chuckles** while listening. Wait for confirmed **Muted**, then
    speak: there must be no reply or visitor snapshot. Manual camera refresh
    and effects must still work. Unmute and verify only new speech is answered.
    Mute during an existing reply: it should finish without starting another
    listening turn until unmuted. Test a pause over 45 seconds while muted to
    check history expiry. Stop the speech process and confirm the phone shows
    unavailable, not a successful mute status. Restart it and verify listening
    is enabled again.
11. After a reset, speak without his name: there should be no reply or visitor
    snapshot. Say "Hey Chuckles" and verify a reply; ask a follow-up without
    his name within 45 seconds and verify it is answered. Wait at least
    45 seconds and verify only another name-addressed prompt starts a turn.
    Exact `quit` and `new visitors` still work without his name.

Hardware-independent regression tests:

```bash
python -m pip install -r requirements-speech.txt -r requirements-web.txt
python -m unittest discover -s tests -v
```

These tests use fake microphone/synthesis/GPIO objects. They do not prove Pi
audio compatibility, real voice quality, network latency, echo cancellation,
or a live Azure deployment. Those require the on-device acceptance check above.