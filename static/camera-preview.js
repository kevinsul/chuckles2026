(() => {
    const panel = document.getElementById("camera-preview");
    const image = document.getElementById("camera-image");
    const status = document.getElementById("camera-status");
    const refresh = document.getElementById("camera-refresh");
    let busy = false;
    let imageUrl = null;
    let version = null;
    let expiry = null;

    function clearImage() {
        clearTimeout(expiry);
        image.hidden = true;
        image.removeAttribute("src");
        if (imageUrl !== null) {
            URL.revokeObjectURL(imageUrl);
        }
        imageUrl = null;
        version = null;
    }

    function showExpiry(remaining, capturedAt, source) {
        clearTimeout(expiry);
        if (remaining <= 0) {
            clearImage();
            status.textContent = "Snapshot expired. Speak to Chuckles or refresh the camera view.";
            return;
        }
        const label = source === "visitor"
            ? "Visitor prompt snapshot"
            : "Manual aiming snapshot (not sent to the model)";
        status.textContent = `${label}. Captured ${new Date(capturedAt * 1000).toLocaleTimeString()}.`;
        expiry = setTimeout(() => {
            clearImage();
            status.textContent = "Snapshot expired. Speak to Chuckles or refresh the camera view.";
        }, remaining * 1000);
    }

    async function update(manual = false) {
        if (busy || document.hidden) {
            return;
        }
        busy = true;
        refresh.disabled = true;
        if (manual) {
            status.textContent = "Capturing a fresh aiming snapshot...";
        }
        try {
            const started = performance.now();
            const response = await fetch(
                manual ? panel.dataset.refreshUrl : panel.dataset.statusUrl,
                {
                    method: manual ? "POST" : "GET",
                    headers: manual ? {"X-Camera-Preview": "1"} : {},
                    cache: "no-store",
                    signal: AbortSignal.timeout(manual ? 12000 : 4000),
                }
            );
            const state = await response.json();
            if (!response.ok) {
                throw new Error(state.error || "Camera preview request failed.");
            }
            if (document.hidden) {
                return;
            }
            if (!state.available) {
                clearImage();
                status.textContent = state.error
                    || "No current snapshot. Speak to Chuckles or refresh the camera view.";
                return;
            }
            const nextVersion = `${state.captured_at}:${state.version}`;
            if (version === nextVersion) {
                showExpiry(
                    state.remaining_seconds - (performance.now() - started) / 1000,
                    state.captured_at, state.source
                );
                return;
            }
            const imageStarted = performance.now();
            const frame = await fetch(panel.dataset.imageUrl, {
                cache: "no-store",
                signal: AbortSignal.timeout(4000),
            });
            if (!frame.ok || frame.headers.get("Content-Type") !== "image/jpeg") {
                throw new Error("Snapshot expired or became unavailable. Try again.");
            }
            const blob = await frame.blob();
            if (document.hidden) {
                return;
            }
            clearImage();
            imageUrl = URL.createObjectURL(blob);
            image.src = imageUrl;
            image.hidden = false;
            const capturedAt = Number(frame.headers.get("X-Preview-Captured-At"));
            version = `${capturedAt}:${frame.headers.get("X-Preview-Version")}`;
            showExpiry(
                Number(frame.headers.get("X-Preview-Remaining"))
                    - (performance.now() - imageStarted) / 1000,
                capturedAt, frame.headers.get("X-Preview-Source")
            );
        } catch (error) {
            clearImage();
            status.textContent = `Camera preview unavailable: ${error.message}`;
        } finally {
            busy = false;
            refresh.disabled = false;
        }
    }

    image.addEventListener("error", () => {
        clearImage();
        status.textContent = "Camera image could not be displayed. Try another capture.";
    });
    refresh.addEventListener("click", () => update(true));
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            clearImage();
        } else {
            update();
        }
    });
    window.addEventListener("pagehide", clearImage);
    setInterval(() => update(), 5000);
    update();
})();

(() => {
    const panel = document.getElementById("listening-controls");
    const status = document.getElementById("listening-status");
    const toggle = document.getElementById("listening-toggle");
    let muted = null;
    let busy = false;

    async function update(action = null) {
        if (busy || document.hidden) {
            return;
        }
        busy = true;
        toggle.disabled = true;
        if (action !== null) {
            status.textContent = action === "mute" ? "Muting Chuckles..." : "Enabling listening...";
        }
        try {
            const url = action === null
                ? panel.dataset.statusUrl
                : action === "mute" ? panel.dataset.muteUrl : panel.dataset.unmuteUrl;
            const response = await fetch(url, {
                method: action === null ? "GET" : "POST",
                headers: action === null ? {} : {"X-Camera-Preview": "1"},
                cache: "no-store",
                signal: AbortSignal.timeout(action === null ? 4000 : 8000),
            });
            const state = await response.json();
            if (!response.ok) {
                throw new Error(state.error || "Listening control request failed.");
            }
            if (typeof state.muted !== "boolean" || typeof state.recognition_stopped !== "boolean") {
                throw new Error("Unrecognized listening status. Update and restart the speech process.");
            }
            muted = state.muted;
            const pending = muted && !state.recognition_stopped;
            status.textContent = pending
                ? "Muting requested; waiting for microphone recognition to stop."
                : muted ? "Muted - Chuckles is not listening." : "Listening enabled.";
            toggle.textContent = pending ? "Muting..." : muted ? "Unmute Chuckles" : "Mute Chuckles";
            toggle.disabled = pending;
        } catch (error) {
            muted = null;
            toggle.textContent = "Unavailable";
            toggle.disabled = true;
            status.textContent = `Listening status unavailable: ${error.message}`;
        } finally {
            busy = false;
        }
    }

    toggle.addEventListener("click", () => {
        if (muted !== null) {
            update(muted ? "unmute" : "mute");
        }
    });
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
            update();
        }
    });
    setInterval(() => update(), 5000);
    update();
})();
