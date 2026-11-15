cmake_minimum_required(VERSION 3.16.3...3.19.7 FATAL_ERROR)

project(HandsHover)

#-----------------------------------------------------------------------------
# Extension meta-information
set(EXTENSION_HOMEPAGE "https://github.com/VOTRE-COMPTE/SlicerHandsHover")
set(EXTENSION_CATEGORY "IGT")
set(EXTENSION_CONTRIBUTORS "TODO: votre nom (votre établissement)")
set(EXTENSION_DESCRIPTION "Touchless control of Slicer views with hand gestures captured by a webcam (MediaPipe hand tracking): slice scrolling, window/level, 3D view rotation/pan/zoom and a mouse mode. Research use only.")
set(EXTENSION_ICONURL "https://raw.githubusercontent.com/VOTRE-COMPTE/SlicerHandsHover/main/HandsHover.png")
set(EXTENSION_SCREENSHOTURLS "")
set(EXTENSION_DEPENDS "NA") # Specified as a list or "NA" if no dependencies

#-----------------------------------------------------------------------------
# Extension dependencies
find_package(Slicer REQUIRED)
include(${Slicer_USE_FILE})

#-----------------------------------------------------------------------------
# Extension modules
add_subdirectory(HandsHover)
## NEXT_MODULE

#-----------------------------------------------------------------------------
include(${Slicer_EXTENSION_GENERATE_CONFIG})
include(${Slicer_EXTENSION_CPACK})
