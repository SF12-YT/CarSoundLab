cmake_minimum_required(VERSION 3.22.1)
project(carsoundlab_audio LANGUAGES CXX)

add_library(carsoundlab_audio SHARED native_audio.cpp)

target_compile_features(carsoundlab_audio PRIVATE cxx_std_17)
target_compile_options(carsoundlab_audio PRIVATE -Wall -Wextra -Werror=return-type)

find_package(oboe REQUIRED CONFIG)
find_library(log-lib log)
find_library(android-lib android)

target_link_libraries(carsoundlab_audio PRIVATE oboe::oboe ${log-lib} ${android-lib})
