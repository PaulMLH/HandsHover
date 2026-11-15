cmake_minimum_required(VERSION 3.16.3...3.19.7 FATAL_ERROR)

project(HandsHover)

#-----------------------------------------------------------------------------
# Extension meta-information
set(EXTENSION_HOMEPAGE "https://github.com/VOTRE-COMPTE/SlicerHandsHover")
set(EXTENSION_CATEGORY "IGT")
set(EXTENSION_CONTRIBUTORS "Dr Paul Martre (Le Havre, France)")
set(EXTENSION_DESCRIPTION "One-handed touchless control of Slicer views with hand gestures filmed by an ordinary webcam (MediaPipe hand tracking): switch views, scroll slices, zoom and pan, rotate the 3D view. Designed for sterile settings. Research use only.")
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
