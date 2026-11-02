# Description: This is the main python file for the web server that controls the GPIO pins on the Raspberry Pi.

# Import the necessary libraries
import os
import sys
import time
import random
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from flask import Flask, render_template, request
from dotenv import load_dotenv
import gpiod
import time

load_dotenv(Path(__file__).resolve().with_name(".env"), override=False, interpolate=False)
preview_port = int(os.environ.get("CAMERA_PREVIEW_PORT", "8765"))
if not 1 <= preview_port <= 65535:
    raise ValueError("CAMERA_PREVIEW_PORT must be between 1 and 65535")

#Configure access to GPIO stuff.
chip = gpiod.Chip('gpiochip4')

#set up smoke pin...
# GPIO 8 is where I have the relay connected to the RPi that turns the smoke machine on and off.
smoke_pin = 8
smoke_line = chip.get_line(smoke_pin)
smoke_line.request(consumer="LED", type=gpiod.LINE_REQ_DIR_OUT)

#set up fire pin...
# GPIO 7 is where I have the relay connected to the RPi that turns the fire machine on and off.
fire_pin = 7
fire_line = chip.get_line(fire_pin)
fire_line.request(consumer="LED", type=gpiod.LINE_REQ_DIR_OUT)

#set up horn pin...
# GPIO 9 is where I have the relay connected to the RPi that turns the horn on and off.
horn_pin = 9
horn_line = chip.get_line(horn_pin)
horn_line.request(consumer="LED", type=gpiod.LINE_REQ_DIR_OUT)

# Create an instance of the Flask class
app = Flask(__name__)


def proxy_preview(path, method="GET"):
    listening_control = path.startswith("listening/")
    upstream = Request(
        f"http://127.0.0.1:{preview_port}/{path}",
        method=method,
        headers={"X-Camera-Preview": "1"},
    )
    try:
        with urlopen(upstream, timeout=10 if method == "POST" else 2) as response:
            result = app.response_class(
                response.read(),
                status=response.status,
                content_type=response.headers.get("Content-Type", "application/json"),
            )
            for name in (
                "X-Preview-Captured-At", "X-Preview-Source",
                "X-Preview-Version", "X-Preview-Remaining",
            ):
                if name in response.headers:
                    result.headers[name] = response.headers[name]
            return result
    except HTTPError as error:
        with error:
            return app.response_class(
                error.read(), status=error.code,
                content_type=error.headers.get("Content-Type", "application/json"),
            )
    except (URLError, TimeoutError, ConnectionError) as error:
        app.logger.warning("Chuckles control service unavailable: %s", type(error).__name__)
        return {
            "error": (
                "Listening controls unavailable. Start the updated speech process."
                if listening_control else
                "Preview unavailable. Start the speech process with camera preview enabled."
            )
        }, 503


@app.after_request
def prevent_preview_caching(response):
    if request.path.startswith(("/camera/", "/listening/")):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/camera/status")
def camera_status():
    return proxy_preview("status")


@app.route("/camera/image")
def camera_image():
    return proxy_preview("image")


@app.route("/camera/refresh", methods=["POST"])
def camera_refresh():
    if request.headers.get("X-Camera-Preview") != "1":
        return {"error": "Preview request header required."}, 403
    return proxy_preview("refresh", "POST")


@app.route("/listening/status")
def listening_status():
    return proxy_preview("listening/status")


@app.route("/listening/<action>", methods=["POST"])
def set_listening(action):
    if action not in ("mute", "unmute"):
        return {"error": "Unknown listening action."}, 404
    if request.headers.get("X-Camera-Preview") != "1":
        return {"error": "Control request header required."}, 403
    return proxy_preview(f"listening/{action}", "POST")


# Define the home route
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        input_text = request.form["input"]
        # Do something with input_text here
    return render_template("home.html")

# Define the firebutton route
@app.route('/firebutton', methods=['POST'])
def firebutton():
    if request.method == 'POST':
        # Do something here
        fire_line.set_value(1)
        time.sleep(1)
        fire_line.set_value(0)
        pass
    return render_template("home.html")

# Define the smokebutton route
@app.route('/smokebutton', methods=['POST'])
def smokebutton():
    if request.method == 'POST':
        # Do something here
        smoke_line.set_value(1)
        time.sleep(4)
        smoke_line.set_value(0)
        pass
    return render_template("home.html")

# Define the hornbutton route
@app.route('/hornbutton', methods=['POST'])
def hornbutton():
    if request.method == 'POST':
        # Do something here
        horn_line.set_value(1)
        time.sleep(1.5)
        horn_line.set_value(0)
        pass
    return render_template("home.html")

# Define the donotpressbutton route
@app.route('/donotpressbutton', methods=['POST'])
def donotpressbutton():
    if request.method == 'POST':
        # Do something here
        pass
    return render_template("home.html")

# Run the app
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
