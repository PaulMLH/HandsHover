#-----------------------------------------------------------------------------
set(MODULE_NAME HandsHover)

#-----------------------------------------------------------------------------
set(MODULE_PYTHON_SCRIPTS
  ${MODULE_NAME}.py
  ${MODULE_NAME}Lib/__init__.py
  ${MODULE_NAME}Lib/actions.py
  ${MODULE_NAME}Lib/filters.py
  ${MODULE_NAME}Lib/gestures.py
  ${MODULE_NAME}Lib/hands.py
  ${MODULE_NAME}Lib/overlay.py
  ${MODULE_NAME}Lib/selftests.py
  ${MODULE_NAME}Lib/synthetic.py
  ${MODULE_NAME}Lib/tracker_process.py
  )

set(MODULE_PYTHON_RESOURCES
  Resources/Icons/${MODULE_NAME}.png
  Resources/requirements.txt
  )

# Bundled MediaPipe model if present (otherwise a download is offered on first use)
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/Resources/Models/hand_landmarker.task)
  list(APPEND MODULE_PYTHON_RESOURCES Resources/Models/hand_landmarker.task)
endif()

#-----------------------------------------------------------------------------
slicerMacroBuildScriptedModule(
  NAME ${MODULE_NAME}
  SCRIPTS ${MODULE_PYTHON_SCRIPTS}
  RESOURCES ${MODULE_PYTHON_RESOURCES}
  WITH_GENERIC_TESTS
  )

#-----------------------------------------------------------------------------
if(BUILD_TESTING)
  # The built-in test (gesture engine + Slicer actions) does not need a camera.
  slicer_add_python_unittest(SCRIPT ${MODULE_NAME}.py)
endif()
