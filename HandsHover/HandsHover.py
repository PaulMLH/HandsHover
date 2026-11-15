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
from HandsHoverLib.gestures import MOUSE, GesturePipeline

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
TRACKER_SCRIPT = os.path.join(MODULE_DIR, "HandsHoverLib", "tracker_process.py")
REQUIREMENTS_FILE = os.path.join(MODULE_DIR, "Resources", "requirements.txt")
BUNDLED_MODEL = os.path.join(MODULE_DIR, "Resources", "Models", "hand_landmarker.task")
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")

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
    "invertHands": False,
    "invertScroll": False,
    "preview": True,
    "scrollStepSlow": 2.0,     # % de la hauteur d'image par coupe
    "scrollStepFast": 0.5,
    "contrastGain": 1.0,
    "rotateGain": 180.0,
    "zoomGain": 1.0,
    "panGain": 1.0,
    "smoothing": 1.5,
    "mouseZoneSize": 1.0,
    "clickMaxMs": 400.0,
    "modelPath": "",
}

BASE_MOUSE_ZONE = (0.40, 0.15, 0.95, 0.80)

GESTURES_HTML = """
<p><b>Pince</b> = signe « OK » (pouce et index joints, trois autres doigts tendus).
Les gestes « maintenus » remplissent une jauge dans le bandeau.</p>
<table border="0" cellspacing="2" cellpadding="3">
<tr><td colspan="2"><b>Partout</b></td></tr>
<tr><td>Deux index levés, 1 s</td><td>Pause / reprise</td></tr>
<tr><td>Index gauche levé, 0,5 s</td><td>Vue suivante : 3D → Red → Green → Yellow</td></tr>
<tr><td>V gauche, 0,5 s</td><td>Entrer / sortir du mode souris</td></tr>
<tr><td colspan="2"><b>Vue 3D</b></td></tr>
<tr><td>Poing gauche + déplacer</td><td>Rotation</td></tr>
<tr><td>Poing droit + déplacer</td><td>Déplacer le modèle</td></tr>
<tr><td>Pince gauche + haut/bas</td><td>Zoom rapide</td></tr>
<tr><td>Pince droite + haut/bas</td><td>Zoom fin</td></tr>
<tr><td>Index droit levé, 1 s</td><td>Recentrer</td></tr>
<tr><td colspan="2"><b>Vues 2D (Red, Green, Yellow)</b></td></tr>
<tr><td>Pince gauche + haut/bas</td><td>Défilement rapide</td></tr>
<tr><td>Pince droite + haut/bas</td><td>Défilement lent</td></tr>
<tr><td>Poing droit + déplacer</td><td>Contraste (horizontal), luminosité (vertical)</td></tr>
<tr><td>Index droit levé, 1 s</td><td>Contraste d'origine</td></tr>
<tr><td colspan="2"><b>Mode souris</b></td></tr>
<tr><td>Main droite</td><td>Pointeur</td></tr>
<tr><td>Pince droite brève</td><td>Clic gauche (deux rapides = double-clic)</td></tr>
<tr><td>Pince droite + déplacer</td><td>Glisser</td></tr>
<tr><td>Pince gauche brève</td><td>Clic droit</td></tr>
<tr><td>Pince gauche + haut/bas</td><td>Molette</td></tr>
</table>
"""


# --------------------------------------------------------------------- réglages
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
        self.parent.contributors = ["À compléter (chirurgie digestive)"]
        self.parent.helpText = (
            "Pilote les vues de Slicer sans contact, avec les mains filmées par une webcam "
            "(suivi MediaPipe). Coupes, contraste, vue 3D et mode souris. "
            "Outil de recherche, non validé pour un usage clinique.")
        self.parent.acknowledgementText = (
            "Suivi des mains : Google MediaPipe (licence Apache 2.0).")


# ======================================================================= widget
class HandsHoverWidget(ScriptedLoadableModuleWidget):
    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)
        self.logic = HandsHoverLogic()
        self.logic.previewCallback = self.onPreview
        self.logic.statusCallback = self.onStatus
        self.logic.stoppedCallback = self.onStopped
        self._cameras = []

        # ---- Contrôle
        box = ctk.ctkCollapsibleButton()
        box.text = "HandsHover – contrôle gestuel"
        self.layout.addWidget(box)
        form = qt.QFormLayout(box)

        self.enableButton = qt.QPushButton("Activer le contrôle gestuel")
        self.enableButton.setCheckable(True)
        self.enableButton.setMinimumHeight(44)
        self.enableButton.setStyleSheet("font-weight: bold; font-size: 14px;")
        form.addRow(self.enableButton)

        self.cameraCombo = qt.QComboBox()
        self.refreshButton = qt.QPushButton("Rafraîchir")
        self.refreshButton.toolTip = "Rechercher les caméras branchées (y compris caméras virtuelles)"
        cameraRow = qt.QHBoxLayout()
        cameraRow.addWidget(self.cameraCombo, 1)
        cameraRow.addWidget(self.refreshButton)
        form.addRow("Caméra :", cameraRow)

        self.invertHandsCheck = qt.QCheckBox("Inverser gauche / droite")
        self.invertHandsCheck.toolTip = "À cocher si les étiquettes G / D de l'aperçu sont inversées"
        form.addRow(self.invertHandsCheck)

        self.previewCheck = qt.QCheckBox("Afficher l'image de la caméra")
        form.addRow(self.previewCheck)

        self.previewLabel = qt.QLabel("Aperçu caméra")
        self.previewLabel.setMinimumSize(320, 180)
        self.previewLabel.setAlignment(qt.Qt.AlignCenter)
        self.previewLabel.setStyleSheet("background-color: #222; color: #999;")
        form.addRow(self.previewLabel)

        self.statusLabel = qt.QLabel("Désactivé.")
        self.statusLabel.wordWrap = True
        form.addRow("État :", self.statusLabel)

        # ---- Réglages
        settingsBox = ctk.ctkCollapsibleButton()
        settingsBox.text = "Réglages"
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

        addSlider("scrollStepSlow", "Défilement lent :", 0.5, 6.0, 0.1, 1, " % / coupe",
                  "Course de la main (en % de la hauteur de l'image) pour avancer d'une coupe. Plus grand = plus fin.")
        addSlider("scrollStepFast", "Défilement rapide :", 0.1, 2.0, 0.05, 2, " % / coupe",
                  "Course de la main pour une coupe en défilement rapide.")
        addSlider("contrastGain", "Contraste :", 0.1, 3.0, 0.1, 1, " ×", "Sensibilité du réglage de contraste / luminosité.")
        addSlider("rotateGain", "Rotation 3D :", 45, 540, 15, 0, " °",
                  "Degrés de rotation pour une traversée complète de l'image.")
        addSlider("zoomGain", "Zoom 3D :", 0.2, 3.0, 0.1, 1, " ×", "Sensibilité du zoom.")
        addSlider("panGain", "Déplacement 3D :", 0.2, 3.0, 0.1, 1, " ×", "Sensibilité du déplacement du modèle.")
        addSlider("smoothing", "Lissage :", 0.3, 5.0, 0.1, 1, "",
                  "Plus petit = plus stable mais plus lent ; plus grand = plus réactif mais plus de tremblement.")
        addSlider("mouseZoneSize", "Zone souris :", 0.4, 1.4, 0.05, 2, " ×",
                  "Taille de la zone de la main droite qui couvre toute la fenêtre. Plus petit = moins de mouvement du bras.")
        addSlider("clickMaxMs", "Durée max d'un clic :", 200, 800, 50, 0, " ms",
                  "Une pince plus longue que ça (sans bouger) n'est pas un clic.")

        self.invertScrollCheck = qt.QCheckBox("Inverser le sens du défilement")
        settingsForm.addRow(self.invertScrollCheck)
        self.resetSettingsButton = qt.QPushButton("Valeurs par défaut")
        settingsForm.addRow(self.resetSettingsButton)
        self.selfTestButton = qt.QPushButton("Tester le moteur de gestes (sans caméra)")
        settingsForm.addRow(self.selfTestButton)

        # ---- Aide
        helpBox = ctk.ctkCollapsibleButton()
        helpBox.text = "Gestes"
        helpBox.collapsed = True
        self.layout.addWidget(helpBox)
        helpLayout = qt.QVBoxLayout(helpBox)
        helpLabel = qt.QLabel(GESTURES_HTML)
        helpLabel.wordWrap = True
        helpLabel.setTextFormat(qt.Qt.RichText)
        helpLayout.addWidget(helpLabel)

        self.layout.addStretch(1)

        # ---- État initial et connexions
        self.invertHandsCheck.checked = getSetting("invertHands")
        self.invertScrollCheck.checked = getSetting("invertScroll")
        self.previewCheck.checked = getSetting("preview")
        savedName = getSetting("cameraName")
        if savedName:
            self._cameras = [{"index": getSetting("cameraIndex"), "backend": getSetting("cameraBackend"), "name": savedName}]
            self.cameraCombo.addItem(savedName)
        else:
            self.cameraCombo.addItem("(cliquer sur Rafraîchir)")

        self.enableButton.connect("toggled(bool)", self.onEnableToggled)
        self.refreshButton.connect("clicked()", self.onRefreshCameras)
        self.cameraCombo.connect("currentIndexChanged(int)", self.onCameraChanged)
        self.invertHandsCheck.connect("toggled(bool)", lambda v: self.onSettingChanged("invertHands", v))
        self.invertScrollCheck.connect("toggled(bool)", lambda v: self.onSettingChanged("invertScroll", v))
        self.previewCheck.connect("toggled(bool)", self.onPreviewToggled)
        self.resetSettingsButton.connect("clicked()", self.onResetSettings)
        self.selfTestButton.connect("clicked()", self.onSelfTest)

    def cleanup(self):
        if hasattr(self, "logic") and self.logic:
            self.logic.shutdown()

    # ------------------------------------------------------------ réglages
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
            self.previewLabel.setText("Aperçu désactivé")

    # ------------------------------------------------------------- caméras
    def onRefreshCameras(self):
        if not self.logic.ensureDependencies():
            return
        qt.QApplication.setOverrideCursor(qt.Qt.WaitCursor)
        try:
            cameras = self.logic.listCameras()
        except Exception as e:
            qt.QApplication.restoreOverrideCursor()
            slicer.util.errorDisplay(f"Impossible de lister les caméras :\n{e}")
            return
        qt.QApplication.restoreOverrideCursor()
        self._setCameraList(cameras)
        if not cameras:
            slicer.util.warningDisplay("Aucune caméra trouvée. Vérifiez qu'elle est branchée et non utilisée par un autre logiciel.")

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
            self.cameraCombo.addItem("(aucune caméra)")
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
            # Redémarre sur la nouvelle caméra
            self._uncheckEnable()
            self.logic.stop()
            self.enableButton.setChecked(True)

    # ---------------------------------------------------------- activation
    def _uncheckEnable(self):
        self.enableButton.blockSignals(True)
        self.enableButton.setChecked(False)
        self.enableButton.blockSignals(False)
        self.enableButton.setText("Activer le contrôle gestuel")

    def onEnableToggled(self, checked):
        if not checked:
            self.logic.stop()
            self.enableButton.setText("Activer le contrôle gestuel")
            self.statusLabel.setText("Désactivé.")
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
                slicer.util.errorDisplay("Aucune caméra disponible.")
                self._uncheckEnable()
                return
            self.logic.applySettings(self.currentSettings())
            self.logic.start(camera, preview=self.previewCheck.checked)
            self.enableButton.setText("Désactiver le contrôle gestuel")
            self.statusLabel.setText(f"Démarrage sur « {camera['name']} »…")
        except Exception as e:
            logging.exception("HandsHover : démarrage impossible")
            self._uncheckEnable()
            slicer.util.errorDisplay(f"Démarrage impossible :\n{e}")

    def _askForModel(self):
        box = qt.QMessageBox(slicer.util.mainWindow())
        box.setWindowTitle("Modèle de suivi des mains")
        box.setText("Le modèle de suivi des mains (hand_landmarker.task, environ 8 Mo) est introuvable.")
        box.setInformativeText(
            "Vous pouvez le télécharger depuis le site de Google (MediaPipe), "
            "ou choisir un fichier hand_landmarker.task déjà présent sur ce PC.")
        box.addButton("Télécharger", qt.QMessageBox.AcceptRole)
        box.addButton("Choisir le fichier…", qt.QMessageBox.ActionRole)
        box.addButton("Annuler", qt.QMessageBox.RejectRole)
        box.exec_()
        role = box.buttonRole(box.clickedButton())
        if role == qt.QMessageBox.AcceptRole:
            qt.QApplication.setOverrideCursor(qt.Qt.WaitCursor)
            try:
                self.logic.downloadModel()
            except Exception as e:
                qt.QApplication.restoreOverrideCursor()
                slicer.util.errorDisplay(f"Téléchargement impossible :\n{e}")
                return False
            qt.QApplication.restoreOverrideCursor()
            return True
        if role == qt.QMessageBox.ActionRole:
            path = qt.QFileDialog.getOpenFileName(
                slicer.util.mainWindow(), "Choisir hand_landmarker.task", "", "Modèle MediaPipe (*.task)")
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
        self.statusLabel.setText("Arrêté." if not error else f"Arrêté : {error}")
        if error:
            slicer.util.errorDisplay(f"Le suivi des mains s'est arrêté :\n{error}")

    def onSelfTest(self):
        from HandsHoverLib.selftests import run_all
        lines = []
        failures = run_all(log=lines.append)
        text = "\n".join(lines)
        if failures:
            slicer.util.errorDisplay("Des tests ont échoué :\n\n" + text)
        else:
            slicer.util.infoDisplay("Tous les tests du moteur de gestes sont passés.\n\n" + text)


# ======================================================================== logic
class HandsHoverLogic(ScriptedLoadableModuleLogic):
    """Lance le processus de suivi, reçoit les mains, fait tourner le moteur de gestes
    et exécute les actions dans Slicer."""

    def __init__(self):
        ScriptedLoadableModuleLogic.__init__(self)
        self.pipeline = GesturePipeline()
        self.actions = SlicerActions()
        self.mouse = None
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

    # ------------------------------------------------------------ chemins
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

    # --------------------------------------------------------- dépendances
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
                "Le contrôle gestuel a besoin de bibliothèques Python qui ne sont pas encore installées :\n\n"
                f"  {', '.join(toInstall)}\n\n"
                "Les installer maintenant ? (connexion internet nécessaire, quelques minutes ; "
                "Slicer peut demander à redémarrer.)",
                windowTitle="HandsHover"):
            self._skipCameraNames = True
            return not missing   # sans les noms de caméras, on peut quand même fonctionner
        numpyBefore = _distributionVersion("numpy")
        try:
            for requirement in missing:
                slicer.util.pip_install(requirement)
                # mediapipe peut installer OpenCV lui-même : on revérifie avant d'en ajouter un
                if requirement == MEDIAPIPE_REQUIREMENT and any(_distributionInstalled(d) for d in OPENCV_DISTRIBUTIONS):
                    break
            if any(r == OPENCV_REQUIREMENT for r in self.missingDependencies()):
                slicer.util.pip_install(OPENCV_REQUIREMENT)
        except Exception as e:
            slicer.util.errorDisplay(f"L'installation a échoué :\n{e}")
            return False
        if needCameraNames:
            try:
                # Sans dépendances, pour ne pas installer une seconde variante d'OpenCV
                slicer.util.pip_install(f"--no-deps {CAMERA_LIST_DISTRIBUTION}")
            except Exception:
                logging.warning("HandsHover : noms des caméras indisponibles (cv2_enumerate_cameras).")
        importlib.invalidate_caches()
        numpyAfter = _distributionVersion("numpy")
        if numpyBefore and numpyAfter and numpyBefore.split(".")[0] != numpyAfter.split(".")[0]:
            slicer.util.warningDisplay(
                f"Attention : l'installation a changé la version de numpy ({numpyBefore} → {numpyAfter}). "
                "Redémarrez Slicer ; en cas de problème, réinstallez Slicer.")
        return not self.missingDependencies()

    # ------------------------------------------------------------- caméras
    def listCameras(self):
        exe = self.pythonSlicerExecutable()
        if not exe:
            raise RuntimeError("PythonSlicer introuvable.")
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
        raise RuntimeError((proc.stderr or "réponse vide")[-800:])

    # -------------------------------------------------------------- réglages
    def applySettings(self, s):
        e = self.pipeline.engine.cfg
        e.scroll_step_slow = s["scrollStepSlow"] / 100.0
        e.scroll_step_fast = s["scrollStepFast"] / 100.0
        e.invert_scroll = bool(s["invertScroll"])
        e.rotate_gain_deg = s["rotateGain"]
        e.zoom_gain_fast = 4.0 * s["zoomGain"]
        e.zoom_gain_fine = 1.5 * s["zoomGain"]
        e.pan_gain = s["panGain"]
        e.click_max_s = s["clickMaxMs"] / 1000.0
        x0, y0, x1, y1 = BASE_MOUSE_ZONE
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        k = s["mouseZoneSize"]
        hw, hh = (x1 - x0) / 2.0 * k, (y1 - y0) / 2.0 * k
        e.mouse_zone = (max(0.0, cx - hw), max(0.0, cy - hh), min(1.0, cx + hw), min(1.0, cy + hh))
        self.pipeline.invert_hands = bool(s["invertHands"])
        self.pipeline.set_filter(min_cutoff=s["smoothing"])
        self.actions.wl_gain = s["contrastGain"]

    # ------------------------------------------------------- démarrage/arrêt
    def start(self, camera, preview=True):
        if self.running:
            self.stop()
        model = self.findModel()
        if not model:
            raise RuntimeError("Modèle hand_landmarker.task introuvable.")
        exe = self.pythonSlicerExecutable()
        if not exe:
            raise RuntimeError("PythonSlicer introuvable.")

        if self.banner is None:
            from HandsHoverLib.overlay import ModeBanner, ViewHighlighter
            self.banner = ModeBanner()
            self.highlighter = ViewHighlighter()
        if self.mouse is None:
            from HandsHoverLib.mouse import MouseController
            self.mouse = MouseController()

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
            raise RuntimeError("Le processus de suivi n'a pas démarré.")

        self.running = True
        for event in self.pipeline.reset():
            self._dispatch(event)
        self.banner.setMode(self.mode, self.paused)
        self.watchdog.start()

    def stop(self, error=None):
        self.watchdog.stop()
        for event in self.pipeline.engine.release():
            self._dispatch(event)
        if self.mouse:
            self.mouse.releaseAll()
        process = self.process
        self.process = None
        self._stopping = True
        if process is not None:
            try:
                process.closeWriteChannel()   # le processus de suivi s'arrête de lui-même
                if not process.waitForFinished(3000):
                    process.kill()
                    process.waitForFinished(1000)
            except Exception:
                logging.exception("HandsHover : arrêt du processus de suivi")
        self.running = False
        self.currentAction = None
        if self.banner:
            self.banner.setOff("HANDSHOVER : ERREUR" if error else "HANDSHOVER DÉSACTIVÉ")
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
                logging.warning("HandsHover : impossible de changer l'aperçu sans redémarrer.")

    # ------------------------------------------------------- messages reçus
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
                self._status(f"Erreur : {self._lastError}")
            elif kind == "status":
                self._status(msg.get("message", ""))
            elif kind == "ready":
                self._status(f"Caméra prête ({msg.get('width')}×{msg.get('height')}). Gestes actifs.")
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
                logging.debug("Suivi des mains : %s", line)
        self._stderrTail = self._stderrTail[-20:]

    def _onFinished(self, exitCode, exitStatus=None):
        if self._stopping or not self.running:
            return
        error = self._lastError or ("\n".join(self._stderrTail[-6:]) or f"code de sortie {exitCode}")
        self.stop(error=error)
        if self.stoppedCallback:
            self.stoppedCallback(error)

    def _onWatchdog(self):
        if not self.running or self._lastFrameWall is None:
            return
        if time.monotonic() - self._lastFrameWall > 3.0:
            self._status("Aucune image de la caméra depuis plus de 3 secondes…")

    def _status(self, text):
        if self.statusCallback:
            self.statusCallback(text)

    # ------------------------------------------------------------ traitement
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
            if event[1] != self.mode and self.mouse:
                self.mouse.releaseAll()
            self.mode, self.paused = event[1], event[2]
            self._updateModeDisplay()
        elif kind == "action":
            self.currentAction = event[1]
            self._updateModeDisplay()
        elif kind == "progress":
            if self.banner:
                self.banner.setProgress(event[1], event[2])
        elif kind in ("pointer", "mouse_button", "mouse_click", "wheel"):
            if self.mouse:
                self.mouse.execute(event)
        else:
            self.actions.execute(event)

    def _updateModeDisplay(self):
        if not self.banner:
            return
        self.banner.setMode(self.mode, self.paused, self.currentAction)
        from HandsHoverLib.overlay import MODE_COLORS
        if self.paused or self.mode == MOUSE:
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
        zone = self.pipeline.engine.cfg.mouse_zone if self.mode == MOUSE else None
        drawHandsOnPixmap(pixmap, self.lastAssigned, self.lastInfo, zone)
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
        self.delayDisplay("Moteur de gestes sur mains synthétiques")
        from HandsHoverLib.selftests import run_all
        failures = run_all(log=logging.info)
        self.assertEqual(failures, [])

    def test_SlicerActions(self):
        self.delayDisplay("Actions sur les vues Slicer")
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
        actions.resetWindowLevel("Red")
        self.assertAlmostEqual(display.GetWindow(), window0, places=1)

        actions.rotate(10.0, 5.0)
        actions.zoom(1.2)
        actions.pan(0.05, 0.0)
        actions.recenter()
        self.delayDisplay("Test réussi")
