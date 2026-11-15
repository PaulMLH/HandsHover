"""Executes gesture commands in Slicer, directly through MRML / VTK
(no keyboard or mouse emulation)."""

import logging
import math

import slicer


def _normalize(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / n, v[1] / n, v[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


class SlicerActions:
    def __init__(self):
        self.wl_gain = 1.0
        self._wl_original = {}   # display node ID -> (window, level, auto)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def layoutManager():
        return slicer.app.layoutManager()

    def sliceLogic(self, view):
        lm = self.layoutManager()
        widget = lm.sliceWidget(view) if lm else None
        return widget.sliceLogic() if widget else None

    def threeDView(self):
        lm = self.layoutManager()
        if not lm or lm.threeDViewCount < 1:
            return None
        widget = lm.threeDWidget(0)
        return widget.threeDView() if widget else None

    def _camera(self):
        view = self.threeDView()
        if view is None:
            return None, None, None
        cameraNode = None
        try:
            cameraNode = slicer.modules.cameras.logic().GetViewActiveCameraNode(view.mrmlViewNode())
        except Exception:
            cameraNode = None
        if cameraNode is not None:
            camera = cameraNode.GetCamera()
        else:
            renderer = view.renderWindow().GetRenderers().GetFirstRenderer()
            camera = renderer.GetActiveCamera()
        return view, cameraNode, camera

    def _render3D(self, view, cameraNode, camera):
        camera.OrthogonalizeViewUp()
        if cameraNode is not None:
            try:
                cameraNode.ResetClippingRange()
            except Exception:
                pass
        else:
            view.renderWindow().GetRenderers().GetFirstRenderer().ResetCameraClippingRange()
        view.scheduleRender()

    # --------------------------------------------------------- dispatch
    def execute(self, event):
        kind = event[0]
        try:
            if kind == "scroll":
                self.scroll(event[1], event[2])
            elif kind == "wl":
                self.windowLevel(event[1], event[2], event[3])
            elif kind == "slice_pan":
                self.slicePan(event[1], event[2], event[3])
            elif kind == "slice_fit":
                self.sliceFit(event[1])
            elif kind == "slice_zoom":
                self.sliceZoom(event[1], event[2])
            elif kind == "wl_reset":
                self.resetWindowLevel(event[1])
            elif kind == "rotate":
                self.rotate(event[1], event[2])
            elif kind == "pan":
                self.pan(event[1], event[2])
            elif kind == "zoom":
                self.zoom(event[1])
            elif kind == "recenter":
                self.recenter()
        except Exception:
            logging.exception("HandsHover: action %s failed", kind)

    # ------------------------------------------------------------- 2D
    def scroll(self, view, n_slices):
        logic = self.sliceLogic(view)
        if logic is None or not n_slices:
            return
        spacing = logic.GetLowestVolumeSliceSpacing()
        step = spacing[2] if spacing and spacing[2] > 0 else 1.0
        offset = logic.GetSliceOffset() + n_slices * step
        bounds = [0.0] * 6
        logic.GetLowestVolumeSliceBounds(bounds)
        if bounds[5] > bounds[4]:
            offset = min(max(offset, bounds[4]), bounds[5])
        logic.SetSliceOffset(offset)
        try:
            logic.SnapSliceOffsetToIJK()
        except Exception:
            pass

    def slicePan(self, view, dx, dy):
        """Pans a slice view in its plane. Hand to the right = image to the right."""
        logic = self.sliceLogic(view)
        if logic is None:
            return
        node = logic.GetSliceNode()
        fov = node.GetFieldOfView()
        sliceToRAS = node.GetSliceToRAS()
        for i in range(3):
            # column 0 = slice x axis (to the right), column 1 = slice y axis (up)
            shift = -dx * fov[0] * sliceToRAS.GetElement(i, 0) + dy * fov[1] * sliceToRAS.GetElement(i, 1)
            sliceToRAS.SetElement(i, 3, sliceToRAS.GetElement(i, 3) + shift)
        node.UpdateMatrices()

    def sliceFit(self, view):
        """Recenters a slice view: fits the displayed volume to the view."""
        logic = self.sliceLogic(view)
        if logic is not None:
            logic.FitSliceToAll()

    def sliceZoom(self, view, factor):
        """factor > 1 = zoom in (the slice field of view shrinks)."""
        logic = self.sliceLogic(view)
        if logic is None or factor <= 0:
            return
        node = logic.GetSliceNode()
        fov = node.GetFieldOfView()
        node.SetFieldOfView(fov[0] / factor, fov[1] / factor, fov[2])

    def _backgroundDisplay(self, view):
        logic = self.sliceLogic(view)
        if logic is None:
            return None, None
        volumeId = logic.GetSliceCompositeNode().GetBackgroundVolumeID()
        volume = slicer.mrmlScene.GetNodeByID(volumeId) if volumeId else None
        display = volume.GetDisplayNode() if volume else None
        if display is None or not display.IsA("vtkMRMLScalarVolumeDisplayNode"):
            return None, None
        return volume, display

    def windowLevel(self, view, dx, dy):
        volume, display = self._backgroundDisplay(view)
        if display is None:
            return
        key = display.GetID()
        if key not in self._wl_original:
            self._wl_original[key] = (display.GetWindow(), display.GetLevel(), display.GetAutoWindowLevel())
        image = volume.GetImageData()
        lo, hi = image.GetScalarRange() if image else (0.0, 1000.0)
        span = max(hi - lo, 1.0) * self.wl_gain
        # Like the mouse in Slicer: horizontal = window width, vertical = level.
        # Hand moving up (dy < 0) = lower level = brighter image.
        window = max(1.0, display.GetWindow() + dx * span)
        level = display.GetLevel() + dy * span
        display.SetAutoWindowLevel(False)
        self._setWindowLevel(display, window, level)

    @staticmethod
    def _setWindowLevel(display, window, level):
        try:
            display.SetWindowLevel(window, level)
        except AttributeError:
            wasModifying = display.StartModify()
            display.SetWindow(window)
            display.SetLevel(level)
            display.EndModify(wasModifying)

    def resetWindowLevel(self, view):
        _volume, display = self._backgroundDisplay(view)
        if display is None:
            return
        original = self._wl_original.get(display.GetID())
        if original is None:
            display.SetAutoWindowLevel(True)
            return
        window, level, auto = original
        if auto:
            display.SetAutoWindowLevel(True)
        else:
            self._setWindowLevel(display, window, level)

    # ------------------------------------------------------------- 3D
    def rotate(self, d_azimuth, d_elevation):
        view, cameraNode, camera = self._camera()
        if camera is None:
            return
        # Limited jump per frame: a badly tracked gesture cannot spin the model at once
        camera.Azimuth(max(-30.0, min(30.0, d_azimuth)))
        camera.Elevation(max(-30.0, min(30.0, d_elevation)))
        self._render3D(view, cameraNode, camera)

    def pan(self, dx, dy):
        view, cameraNode, camera = self._camera()
        if camera is None:
            return
        position = camera.GetPosition()
        focal = camera.GetFocalPoint()
        direction = _normalize([focal[i] - position[i] for i in range(3)])
        right = _normalize(_cross(direction, camera.GetViewUp()))
        up = _normalize(_cross(right, direction))
        if camera.GetParallelProjection():
            height = 2.0 * camera.GetParallelScale()
        else:
            distance = camera.GetDistance()
            height = 2.0 * distance * math.tan(math.radians(camera.GetViewAngle() / 2.0))
        # Hand to the right = model to the right (the camera moves to the left)
        shift = [(-dx * right[i] + dy * up[i]) * height for i in range(3)]
        camera.SetPosition([position[i] + shift[i] for i in range(3)])
        camera.SetFocalPoint([focal[i] + shift[i] for i in range(3)])
        self._render3D(view, cameraNode, camera)

    def zoom(self, factor):
        view, cameraNode, camera = self._camera()
        if camera is None or factor <= 0:
            return
        if camera.GetParallelProjection():
            camera.SetParallelScale(camera.GetParallelScale() / factor)
        else:
            camera.Dolly(factor)
        self._render3D(view, cameraNode, camera)

    def recenter(self):
        view = self.threeDView()
        if view is not None:
            view.resetFocalPoint()
