import importlib
import importlib.metadata
import json
import logging
import os
import shutil
import subprocess
import sys
import time

import ctk
import qt
import slicer
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleLogic,
    ScriptedLoadableModuleTest,
    ScriptedLoadableModuleWidget,
)

from HandsHoverLib.actions import SlicerActions
from HandsHoverLib.gestures import GesturePipeline

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
TRACKER_SCRIPT = os.path.join(MODULE_DIR, "HandsHoverLib", "tracker_process.py")
BUNDLED_MODEL = os.path.join(MODULE_DIR, "Resources", "Models", "hand_landmarker.task")
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")

AUTHOR = "Dr Paul Martre - Le Havre - France"

MEDIAPIPE_REQUIREMENT = "mediapipe>=0.10.31"
OPENCV_REQUIREMENT = "opencv-python-headless"
OPENCV_DISTRIBUTIONS = (
    "opencv-python", "opencv-contrib-python",
    "opencv-python-headless", "opencv-contrib-python-headless",
)
CAMERA_LIST_DISTRIBUTION = "cv2_enumerate_cameras"

SETTINGS_PREFIX = "HandsHover/"
DEFAULT_SETTINGS = {
    "cameraName": "",
    "cameraIndex": 0,
    "cameraBackend": -1,
    "invertScroll": False,
    "preview": True,
    "scrollStep": 2.0,         # % of image height per slice (slow gesture)
    "scrollAccel": 4.0,        # maximum multiplier for a fast gesture
    "swipeDistance": 15.0,     # % of image width for a V swipe
    "contrastGain": 1.0,
    "rotateGain": 180.0,
    "zoomGain": 1.0,
    "panGain": 1.0,
    "smoothing": 1.5,
    "modelPath": "",
}

GESTURES_HTML = """
<p>Everything is done <b>with one hand</b>: the first hand seen controls Slicer,
the other hand is ignored (except for the two-fist pause).<br>
<b>Pinch</b> = OK sign (thumb and index touching, the other three fingers extended).</p>
<table border="0" cellspacing="2" cellpadding="3">
<tr><td colspan="2"><b>Everywhere</b></td></tr>
<tr><td>Two closed fists, 1 s</td><td>Pause / resume all commands</td></tr>
<tr><td>Thumb up, 1 s</td><td>Recenter the controlled view</td></tr>
<tr><td>V + horizontal swipe</td><td>Next (right) / previous (left) view: 3D, Red, Green, Yellow</td></tr>
<tr><td colspan="2"><b>3D view</b></td></tr>
<tr><td>Fist + up/down</td><td>Zoom in / out</td></tr>
<tr><td>Pinch + move</td><td>Rotate the model</td></tr>
<tr><td>Index finger + move</td><td>Pan the model</td></tr>
<tr><td colspan="2"><b>Slice views (Red, Green, Yellow)</b></td></tr>
<tr><td>Pinch + up/down</td><td>Scroll slices (faster gesture = accelerated)</td></tr>
<tr><td>Fist + up/down</td><td>Zoom in / out</td></tr>
<tr><td>Index finger + move</td><td>Window (horizontal), level (vertical)</td></tr>
</table>
"""


# --------------------------------------------------------------------- settings
def getSetting(key):
    default = DEFAULT_SETTINGS[key]
    value = qt.QSettings().value(SETTINGS_PREFIX + key)
    if value is None or value == "":
        return default
    try:
        if isinstance(default, bool):
            return value if isinstance(value, bool) else str(value).lower() in ("true", "1", "yes")
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
        return str(value)
    except (TypeError, ValueError):
        return default


def setSetting(key, value):
    qt.QSettings().setValue(SETTINGS_PREFIX + key, value)


def _distributionInstalled(name):
    for candidate in (name, name.replace("_", "-"), name.replace("-", "_")):
        try:
            importlib.metadata.version(candidate)
            return True
        except importlib.metadata.PackageNotFoundError:
            continue
    return False


def _distributionVersion(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _toBytes(qbytearray):
    for convert in (lambda b: bytes(b.data()), lambda b: bytes(b), lambda b: str(b).encode("utf-8")):
        try:
            return convert(qbytearray)
        except Exception:
            continue
    return b""


# ======================================================================= module
class HandsHover(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "HandsHover"
        self.parent.categories = ["IGT"]
        self.parent.dependencies = []
        self.parent.contributors = [AUTHOR]
        self.parent.helpText = (
            "Touchless, one-handed control of Slicer views with hand gestures filmed by an ordinary "
            "webcam (MediaPipe hand tracking): switch views, scroll slices, zoom, window/level, "
            "rotate and pan the 3D view. Camera images are processed locally and never sent anywhere. "
            "Research tool, not validated for clinical use.<br><br>"
            f"Created by {AUTHOR}.")
        self.parent.acknowledgementText = (
            "Hand tracking: Google MediaPipe (Apache 2.0 license).")


# ======================================================================= widget
class HandsHoverWidget(ScriptedLoadableModuleWidget):
    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)
        self.logic = HandsHoverLogic()
        self.logic.previewCallback = self.onPreview
        self.logic.statusCallback = self.onStatus
        self.logic.stoppedCallback = self.onStopped
        self._cameras = []

        # ---- Control
        box = ctk.ctkCollapsibleButton()
        box.text = "HandsHover"
        self.layout.addWidget(box)
        form = qt.QFormLayout(box)

        self.enableButton = qt.QPushButton("Start gesture control")
        self.enableButton.setCheckable(True)
        self.enableButton.setMinimumHeight(44)
        self.enableButton.setStyleSheet("font-weight: bold; font-size: 14px;")
        form.addRow(self.enableButton)

        self.cameraCombo = qt.QComboBox()
        self.refreshButton = qt.QPushButton("Refresh")
        self.refreshButton.toolTip = "Search for connected cameras (including virtual cameras)"
        cameraRow = qt.QHBoxLayout()
        cameraRow.addWidget(self.cameraCombo, 1)
        cameraRow.addWidget(self.refreshButton)
        form.addRow("Camera:", cameraRow)

        self.previewCheck = qt.QCheckBox("Show camera image")
        form.addRow(self.previewCheck)

        self.previewLabel = qt.QLabel("Camera preview")
        self.previewLabel.setMinimumSize(320, 180)
        self.previewLabel.setAlignment(qt.Qt.AlignCenter)
        self.previewLabel.setStyleSheet("background-color: #222; color: #999;")
        form.addRow(self.previewLabel)

        self.statusLabel = qt.QLabel("Stopped.")
        self.statusLabel.wordWrap = True
        form.addRow("Status:", self.statusLabel)

        # ---- Settings
        settingsBox = ctk.ctkCollapsibleButton()
        settingsBox.text = "Settings"
        settingsBox.collapsed = True
        self.layout.addWidget(settingsBox)
        settingsForm = qt.QFormLayout(settingsBox)
        self.sliders = {}

        def addSlider(key, label, minimum, maximum, step, decimals, suffix, tooltip):
            slider = ctk.ctkSliderWidget()
            slider.minimum = minimum
            slider.maximum = maximum
            slider.singleStep = step
            slider.decimals = decimals
            slider.suffix = suffix
            slider.toolTip = tooltip
            slider.value = getSetting(key)
            slider.connect("valueChanged(double)", lambda value, k=key: self.onSettingChanged(k, value))
            settingsForm.addRow(label, slider)
            self.sliders[key] = slider

        addSlider("scrollStep", "Scrolling:", 0.5, 6.0, 0.1, 1, " % / slice",
                  "Hand travel (in % of the image height) to move by one slice with a slow gesture. Larger = finer.")
        addSlider("scrollAccel", "Acceleration:", 1.0, 8.0, 0.5, 1, " x",
                  "Maximum scrolling multiplier for a fast gesture (1 = no acceleration).")
        addSlider("swipeDistance", "V swipe:", 5.0, 35.0, 1.0, 0, " %",
                  "Horizontal distance (in % of the image width) to change view. Larger = fewer accidental changes.")
        addSlider("contrastGain", "Window/level:", 0.1, 3.0, 0.1, 1, " x", "Window / level sensitivity.")
        addSlider("rotateGain", "3D rotation:", 45, 540, 15, 0, " deg",
                  "Rotation in degrees for a full traversal of the image.")
        addSlider("zoomGain", "Zoom:", 0.2, 3.0, 0.1, 1, " x", "Zoom sensitivity.")
        addSlider("panGain", "3D pan:", 0.2, 3.0, 0.1, 1, " x", "Pan sensitivity.")
        addSlider("smoothing", "Smoothing:", 0.3, 5.0, 0.1, 1, "",
                  "Smaller = steadier but slower; larger = more responsive but more jitter.")

        self.invertScrollCheck = qt.QCheckBox("Invert scrolling direction")
        settingsForm.addRow(self.invertScrollCheck)
        self.resetSettingsButton = qt.QPushButton("Restore defaults")
        settingsForm.addRow(self.resetSettingsButton)

        # ---- Help
        helpBox = ctk.ctkCollapsibleButton()
        helpBox.text = "Gestures"
        helpBox.collapsed = True
        self.layout.addWidget(helpBox)
        helpLayout = qt.QVBoxLayout(helpBox)
        helpLabel = qt.QLabel(GESTURES_HTML)
        helpLabel.wordWrap = True
        helpLabel.setTextFormat(qt.Qt.RichText)
        helpLayout.addWidget(helpLabel)

        self.layout.addStretch(1)

        # ---- Initial state and connections
        self.invertScrollCheck.checked = getSetting("invertScroll")
        self.previewCheck.checked = getSetting("preview")
        savedName = getSetting("cameraName")
        if savedName:
            self._cameras = [{"index": getSetting("cameraIndex"), "backend": getSetting("cameraBackend"), "name": savedName}]
            self.cameraCombo.addItem(savedName)
        else:
            self.cameraCombo.addItem("(click Refresh)")

        self.enableButton.connect("toggled(bool)", self.onEnableToggled)
        self.refreshButton.connect("clicked()", self.onRefreshCameras)
        self.cameraCombo.connect("currentIndexChanged(int)", self.onCameraChanged)
        self.invertScrollCheck.connect("toggled(bool)", lambda v: self.onSettingChanged("invertScroll", v))
        self.previewCheck.connect("toggled(bool)", self.onPreviewToggled)
        self.resetSettingsButton.connect("clicked()", self.onResetSettings)

    def cleanup(self):
        if hasattr(self, "logic") and self.logic:
            self.logic.shutdown()

    # ------------------------------------------------------------ settings
    def currentSettings(self):
        return {key: getSetting(key) for key in DEFAULT_SETTINGS}

    def onSettingChanged(self, key, value):
        setSetting(key, value)
        self.logic.applySettings(self.currentSettings())

    def onResetSettings(self):
        for key, slider in self.sliders.items():
            slider.value = DEFAULT_SETTINGS[key]
        self.invertScrollCheck.checked = DEFAULT_SETTINGS["invertScroll"]

    def onPreviewToggled(self, checked):
        setSetting("preview", checked)
        self.logic.setPreview(checked)
        if not checked:
            self.previewLabel.clear()
            self.previewLabel.setText("Preview off")

    # ------------------------------------------------------------- cameras
    def onRefreshCameras(self):
        if not self.logic.ensureDependencies():
            return
        qt.QApplication.setOverrideCursor(qt.Qt.WaitCursor)
        try:
            cameras = self.logic.listCameras()
        except Exception as e:
            qt.QApplication.restoreOverrideCursor()
            slicer.util.errorDisplay(f"Cannot list cameras:\n{e}")
            return
        qt.QApplication.restoreOverrideCursor()
        self._setCameraList(cameras)
        if not cameras:
            slicer.util.warningDisplay("No camera found. Check that it is connected and not used by another application.")

    def _setCameraList(self, cameras):
        savedName = getSetting("cameraName")
        savedIndex = getSetting("cameraIndex")
        self._cameras = list(cameras)
        self.cameraCombo.blockSignals(True)
        self.cameraCombo.clear()
        selected = 0
        for i, cam in enumerate(self._cameras):
            self.cameraCombo.addItem(f"{cam['name']}")
            if cam["name"] == savedName and (cam["index"] == savedIndex or selected == 0):
                selected = i
        if not self._cameras:
            self.cameraCombo.addItem("(no camera)")
        self.cameraCombo.setCurrentIndex(selected)
        self.cameraCombo.blockSignals(False)
        self.onCameraChanged(selected)

    def currentCamera(self):
        i = self.cameraCombo.currentIndex
        if 0 <= i < len(self._cameras):
            return self._cameras[i]
        return None

    def onCameraChanged(self, index):
        cam = self.currentCamera()
        if cam is None:
            return
        setSetting("cameraName", cam["name"])
        setSetting("cameraIndex", cam["index"])
        setSetting("cameraBackend", cam.get("backend", -1))
        if self.logic.running:
            # Restart on the new camera
            self._uncheckEnable()
            self.logic.stop()
            self.enableButton.setChecked(True)

    # ---------------------------------------------------------- start/stop
    def _uncheckEnable(self):
        self.enableButton.blockSignals(True)
        self.enableButton.setChecked(False)
        self.enableButton.blockSignals(False)
        self.enableButton.setText("Start gesture control")

    def onEnableToggled(self, checked):
        if not checked:
            self.logic.stop()
            self.enableButton.setText("Start gesture control")
            self.statusLabel.setText("Stopped.")
            return
        try:
            if not self.logic.ensureDependencies():
                self._uncheckEnable()
                return
            if not self.logic.findModel() and not self._askForModel():
                self._uncheckEnable()
                return
            if not self._cameras:
                qt.QApplication.setOverrideCursor(qt.Qt.WaitCursor)
                try:
                    self._setCameraList(self.logic.listCameras())
                finally:
                    qt.QApplication.restoreOverrideCursor()
            camera = self.currentCamera()
            if camera is None:
                slicer.util.errorDisplay("No camera available.")
                self._uncheckEnable()
                return
            self.logic.applySettings(self.currentSettings())
            self.logic.start(camera, preview=self.previewCheck.checked)
            self.enableButton.setText("Stop gesture control")
            self.statusLabel.setText(f"Starting on \"{camera['name']}\"...")
        except Exception as e:
            logging.exception("HandsHover: cannot start")
            self._uncheckEnable()
            slicer.util.errorDisplay(f"Cannot start:\n{e}")

    def _askForModel(self):
        box = qt.QMessageBox(slicer.util.mainWindow())
        box.setWindowTitle("Hand-tracking model")
        box.setText("The hand-tracking model (hand_landmarker.task, about 8 MB) was not found.")
        box.setInformativeText(
            "You can download it from Google (MediaPipe), "
            "or select a hand_landmarker.task file already on this computer.")
        box.addButton("Download", qt.QMessageBox.AcceptRole)
        box.addButton("Select file...", qt.QMessageBox.ActionRole)
        box.addButton("Cancel", qt.QMessageBox.RejectRole)
        box.exec_()
        role = box.buttonRole(box.clickedButton())
        if role == qt.QMessageBox.AcceptRole:
            qt.QApplication.setOverrideCursor(qt.Qt.WaitCursor)
            try:
                self.logic.downloadModel()
            except Exception as e:
                qt.QApplication.restoreOverrideCursor()
                slicer.util.errorDisplay(f"Download failed:\n{e}")
                return False
            qt.QApplication.restoreOverrideCursor()
            return True
        if role == qt.QMessageBox.ActionRole:
            path = qt.QFileDialog.getOpenFileName(
                slicer.util.mainWindow(), "Select hand_landmarker.task", "", "MediaPipe model (*.task)")
            if path:
                setSetting("modelPath", path)
                return True
        return False

    # ---------------------------------------------------------- callbacks
    def onPreview(self, pixmap):
        if not self.previewCheck.checked:
            return
        scaled = pixmap.scaled(self.previewLabel.width, self.previewLabel.height,
                               qt.Qt.KeepAspectRatio, qt.Qt.SmoothTransformation)
        self.previewLabel.setPixmap(scaled)

    def onStatus(self, text):
        self.statusLabel.setText(text)

    def onStopped(self, error):
        self._uncheckEnable()
        self.statusLabel.setText("Stopped." if not error else f"Stopped: {error}")
        if error:
            slicer.util.errorDisplay(f"Hand tracking stopped:\n{error}")


# ======================================================================== logic
class HandsHoverLogic(ScriptedLoadableModuleLogic):
    """Starts the tracking process, receives the hands, runs the gesture engine
    and executes the actions in Slicer."""

    def __init__(self):
        ScriptedLoadableModuleLogic.__init__(self)
        self.pipeline = GesturePipeline()
        self.actions = SlicerActions()
        self.banner = None
        self.highlighter = None
        self.process = None
        self.running = False
        self._stopping = False
        self._buffer = b""
        self._stderrTail = []
        self._lastError = None
        self._lastFrameWall = None
        self._skipCameraNames = False
        self.mode = "3D"
        self.paused = False
        self.currentAction = None
        self.lastAssigned = {}
        self.lastInfo = {}
        self.fps = 0.0
        self.previewCallback = None
        self.statusCallback = None
        self.stoppedCallback = None
        self.watchdog = qt.QTimer()
        self.watchdog.setInterval(1000)
        self.watchdog.connect("timeout()", self._onWatchdog)

    # -------------------------------------------------------------- paths
    @staticmethod
    def pythonSlicerExecutable():
        exe = shutil.which("PythonSlicer")
        if exe:
            return exe
        for name in ("PythonSlicer.exe", "PythonSlicer"):
            candidate = os.path.join(slicer.app.slicerHome, "bin", name)
            if os.path.exists(candidate):
                return candidate
        return None

    @staticmethod
    def userModelPath():
        try:
            base = os.path.dirname(slicer.app.slicerUserSettingsFilePath)
        except Exception:
            base = os.path.expanduser("~")
        return os.path.join(base, "HandsHover", "hand_landmarker.task")

    def findModel(self):
        for path in (getSetting("modelPath"), BUNDLED_MODEL, self.userModelPath()):
            if path and os.path.isfile(path):
                return path
        return None

    def downloadModel(self):
        import urllib.request
        path = self.userModelPath()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        urllib.request.urlretrieve(MODEL_URL, tmp)
        os.replace(tmp, path)
        return path

    @staticmethod
    def _environment():
        if hasattr(slicer.util, "startupEnvironment"):
            try:
                return slicer.util.startupEnvironment()
            except Exception:
                pass
        return dict(os.environ)

    # -------------------------------------------------------- dependencies
    def missingDependencies(self):
        missing = []
        if not _distributionInstalled("mediapipe"):
            missing.append(MEDIAPIPE_REQUIREMENT)
        if not any(_distributionInstalled(d) for d in OPENCV_DISTRIBUTIONS):
            missing.append(OPENCV_REQUIREMENT)
        return missing

    def ensureDependencies(self):
        missing = self.missingDependencies()
        needCameraNames = not _distributionInstalled(CAMERA_LIST_DISTRIBUTION) and not self._skipCameraNames
        if not missing and not needCameraNames:
            return True
        toInstall = missing + ([CAMERA_LIST_DISTRIBUTION] if needCameraNames else [])
        if not slicer.util.confirmOkCancelDisplay(
                "HandsHover needs Python packages that are not installed yet:\n\n"
                f"  {', '.join(toInstall)}\n\n"
                "Install them now? (internet connection required, a few minutes; "
                "Slicer may ask to restart.)",
                windowTitle="HandsHover"):
            self._skipCameraNames = True
            return not missing   # works without camera names
        numpyBefore = _distributionVersion("numpy")
        try:
            for requirement in missing:
                slicer.util.pip_install(requirement)
                # MediaPipe may install OpenCV itself: check again before adding one
                if requirement == MEDIAPIPE_REQUIREMENT and any(_distributionInstalled(d) for d in OPENCV_DISTRIBUTIONS):
                    break
            if any(r == OPENCV_REQUIREMENT for r in self.missingDependencies()):
                slicer.util.pip_install(OPENCV_REQUIREMENT)
        except Exception as e:
            slicer.util.errorDisplay(f"Installation failed:\n{e}")
            return False
        if needCameraNames:
            try:
                # Without dependencies, so that a second OpenCV variant is not installed
                slicer.util.pip_install(f"--no-deps {CAMERA_LIST_DISTRIBUTION}")
            except Exception:
                logging.warning("HandsHover: camera names unavailable (cv2_enumerate_cameras).")
        importlib.invalidate_caches()
        numpyAfter = _distributionVersion("numpy")
        if numpyBefore and numpyAfter and numpyBefore.split(".")[0] != numpyAfter.split(".")[0]:
            slicer.util.warningDisplay(
                f"Warning: the installation changed the numpy version ({numpyBefore} -> {numpyAfter}). "
                "Restart Slicer; if problems occur, reinstall Slicer.")
        return not self.missingDependencies()

    # ------------------------------------------------------------- cameras
    def listCameras(self):
        exe = self.pythonSlicerExecutable()
        if not exe:
            raise RuntimeError("PythonSlicer not found.")
        kwargs = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        proc = subprocess.run([exe, TRACKER_SCRIPT, "--list-cameras"], capture_output=True, text=True,
                              timeout=90, env=self._environment(), **kwargs)
        for line in reversed(proc.stdout.splitlines()):
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("type") == "cameras":
                return msg["cameras"]
            if msg.get("type") == "error":
                raise RuntimeError(msg.get("message"))
        raise RuntimeError((proc.stderr or "empty response")[-800:])

    # ------------------------------------------------------------ settings
    def applySettings(self, s):
        e = self.pipeline.engine.cfg
        e.scroll_step = s["scrollStep"] / 100.0
        e.scroll_accel_max = s["scrollAccel"]
        e.swipe_distance = s["swipeDistance"] / 100.0
        e.invert_scroll = bool(s["invertScroll"])
        e.rotate_gain_deg = s["rotateGain"]
        e.zoom_gain = 3.0 * s["zoomGain"]
        e.slice_zoom_gain = 3.0 * s["zoomGain"]
        e.pan_gain = s["panGain"]
        self.pipeline.set_filter(min_cutoff=s["smoothing"])
        self.actions.wl_gain = s["contrastGain"]

    # ---------------------------------------------------------- start/stop
    def start(self, camera, preview=True):
        if self.running:
            self.stop()
        model = self.findModel()
        if not model:
            raise RuntimeError("Model hand_landmarker.task not found.")
        exe = self.pythonSlicerExecutable()
        if not exe:
            raise RuntimeError("PythonSlicer not found.")

        if self.banner is None:
            from HandsHoverLib.overlay import ModeBanner, ViewHighlighter
            self.banner = ModeBanner()
            self.highlighter = ViewHighlighter()

        args = [TRACKER_SCRIPT, "--model", model,
                "--camera", str(int(camera["index"])), "--backend", str(int(camera.get("backend", -1)))]
        if preview:
            args.append("--preview")

        env = qt.QProcessEnvironment()
        for key, value in self._environment().items():
            env.insert(str(key), str(value))

        self._buffer = b""
        self._stderrTail = []
        self._lastError = None
        self._lastFrameWall = None
        self._stopping = False
        self.process = qt.QProcess()
        self.process.setProcessEnvironment(env)
        self.process.connect("readyReadStandardOutput()", self._onStdout)
        self.process.connect("readyReadStandardError()", self._onStderr)
        self.process.connect("finished(int,QProcess::ExitStatus)", self._onFinished)
        self.process.start(exe, args)
        if not self.process.waitForStarted(10000):
            self.process = None
            raise RuntimeError("The hand-tracking process did not start.")

        self.running = True
        for event in self.pipeline.reset():
            self._dispatch(event)
        self.banner.setMode(self.mode, self.paused)
        self.watchdog.start()

    def stop(self, error=None):
        self.watchdog.stop()
        for event in self.pipeline.engine.release():
            self._dispatch(event)
        process = self.process
        self.process = None
        self._stopping = True
        if process is not None:
            try:
                process.closeWriteChannel()   # the tracking process then stops by itself
                if not process.waitForFinished(3000):
                    process.kill()
                    process.waitForFinished(1000)
            except Exception:
                logging.exception("HandsHover: stopping the tracking process")
        self.running = False
        self.currentAction = None
        if self.banner:
            self.banner.setOff("HANDSHOVER: ERROR" if error else "HANDSHOVER OFF")
        if self.highlighter:
            self.highlighter.hide()

    def shutdown(self):
        self.stop()
        if self.banner:
            self.banner.remove()
            self.banner = None
        if self.highlighter:
            self.highlighter.remove()
            self.highlighter = None

    def setPreview(self, on):
        if self.process is None:
            return
        command = b"preview 1\n" if on else b"preview 0\n"
        try:
            self.process.write(command)
        except Exception:
            try:
                self.process.write(qt.QByteArray(command))
            except Exception:
                logging.warning("HandsHover: cannot change the preview without restarting.")

    # ---------------------------------------------------- incoming messages
    def _onStdout(self):
        if self.process is None:
            return
        self._buffer += _toBytes(self.process.readAllStandardOutput())
        lines = self._buffer.split(b"\n")
        self._buffer = lines.pop()
        lastFrame = None
        lastPreview = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "frame":
                lastFrame = msg
            elif kind == "preview":
                lastPreview = msg
            elif kind == "error":
                self._lastError = msg.get("message")
                self._status(f"Error: {self._lastError}")
            elif kind == "status":
                self._status(msg.get("message", ""))
            elif kind == "ready":
                self._status(f"Camera ready ({msg.get('width')}x{msg.get('height')}). Gestures active.")
        if lastFrame is not None:
            self._handleFrame(lastFrame)
        if lastPreview is not None and self.previewCallback:
            self._handlePreview(lastPreview)

    def _onStderr(self):
        if self.process is None:
            return
        text = _toBytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.strip():
                self._stderrTail.append(line)
                logging.debug("HandsHover tracking: %s", line)
        self._stderrTail = self._stderrTail[-20:]

    def _onFinished(self, exitCode, exitStatus=None):
        if self._stopping or not self.running:
            return
        error = self._lastError or ("\n".join(self._stderrTail[-6:]) or f"exit code {exitCode}")
        self.stop(error=error)
        if self.stoppedCallback:
            self.stoppedCallback(error)

    def _onWatchdog(self):
        if not self.running or self._lastFrameWall is None:
            return
        if time.monotonic() - self._lastFrameWall > 3.0:
            self._status("No camera image for more than 3 seconds...")

    def _status(self, text):
        if self.statusCallback:
            self.statusCallback(text)

    # ------------------------------------------------------------ processing
    def _handleFrame(self, msg):
        self._lastFrameWall = time.monotonic()
        self.fps = msg.get("fps", 0.0)
        events, assigned, info = self.pipeline.process(msg["t"], msg.get("hands", []))
        self.lastAssigned, self.lastInfo = assigned, info
        for event in events:
            self._dispatch(event)
        if self.banner:
            self.banner.setHands(info, self.fps)
        if self.highlighter:
            self.highlighter.refresh()

    def _dispatch(self, event):
        kind = event[0]
        if kind == "mode":
            self.mode, self.paused = event[1], event[2]
            self._updateModeDisplay()
        elif kind == "action":
            self.currentAction = event[1]
            self._updateModeDisplay()
        elif kind == "progress":
            if self.banner:
                self.banner.setProgress(event[1], event[2])
        else:
            self.actions.execute(event)

    def _updateModeDisplay(self):
        if not self.banner:
            return
        self.banner.setMode(self.mode, self.paused, self.currentAction)
        from HandsHoverLib.overlay import MODE_COLORS
        if self.paused:
            self.highlighter.hide()
        else:
            self.highlighter.show(self.mode, MODE_COLORS.get(self.mode, "#ffffff"))

    def _handlePreview(self, msg):
        import base64
        from HandsHoverLib.overlay import drawHandsOnPixmap
        data = base64.b64decode(msg.get("jpg", ""))
        pixmap = qt.QPixmap()
        loaded = False
        for payload in (data, qt.QByteArray(data)):
            try:
                if pixmap.loadFromData(payload):
                    loaded = True
                    break
            except Exception:
                continue
        if not loaded:
            return
        drawHandsOnPixmap(pixmap, self.lastAssigned, self.lastInfo)
        self.previewCallback(pixmap)


# ========================================================================= test
class HandsHoverTest(ScriptedLoadableModuleTest):
    def setUp(self):
        slicer.mrmlScene.Clear()

    def runTest(self):
        self.setUp()
        self.test_GestureEngine()
        self.setUp()
        self.test_SlicerActions()

    def test_GestureEngine(self):
        self.delayDisplay("Gesture engine on synthetic hands")
        from HandsHoverLib.selftests import run_all
        failures = run_all(log=logging.info)
        self.assertEqual(failures, [])

    def test_SlicerActions(self):
        self.delayDisplay("Actions on Slicer views")
        import numpy as np
        array = (np.arange(40 * 64 * 64, dtype=np.int16).reshape(40, 64, 64) % 1000)
        volume = slicer.util.addVolumeFromArray(array, name="HandsHoverTestVolume")
        slicer.util.setSliceViewerLayers(background=volume, fit=True)
        actions = SlicerActions()

        sliceLogic = slicer.app.layoutManager().sliceWidget("Red").sliceLogic()
        before = sliceLogic.GetSliceOffset()
        actions.scroll("Red", 5)
        self.assertNotAlmostEqual(before, sliceLogic.GetSliceOffset())

        display = volume.GetDisplayNode()
        window0 = display.GetWindow()
        actions.windowLevel("Red", 0.1, 0.0)
        self.assertGreater(display.GetWindow(), window0)

        sliceNode = sliceLogic.GetSliceNode()
        fov0 = sliceNode.GetFieldOfView()[0]
        actions.sliceZoom("Red", 2.0)
        self.assertAlmostEqual(sliceNode.GetFieldOfView()[0], fov0 / 2.0, places=3)
        actions.sliceFit("Red")
        self.assertAlmostEqual(sliceNode.GetFieldOfView()[0], fov0, delta=fov0 * 0.05)

        actions.rotate(10.0, 5.0)
        actions.zoom(1.2)
        actions.pan(0.05, 0.0)
        actions.recenter()
        self.delayDisplay("Test passed")
