cmake_minimum_required(VERSION 3.16)
project(rex_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

add_library(rex_native SHARED rex_native.cpp)
target_include_directories(rex_native PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

if(WIN32)
  set_target_properties(rex_native PROPERTIES OUTPUT_NAME "rex_native")
elseif(APPLE)
  set_target_properties(rex_native PROPERTIES OUTPUT_NAME "rex_native" PREFIX "lib" SUFFIX ".dylib")
else()
  set_target_properties(rex_native PROPERTIES OUTPUT_NAME "rex_native" PREFIX "lib" SUFFIX ".so")
endif()

# put library next to python wrapper
set_target_properties(rex_native PROPERTIES
  LIBRARY_OUTPUT_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../runtimelib/native
  RUNTIME_OUTPUT_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../runtimelib/native
)

add_executable(rex_native_test test_native.cpp rex_native.cpp)
target_include_directories(rex_native_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

enable_testing()
add_test(NAME rex_native_test COMMAND rex_native_test)
