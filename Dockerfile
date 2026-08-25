FROM emscripten/emsdk:latest AS base-image

# fixes tzdata freeze
ENV DEBIAN_FRONTEND noninteractive
# StackOverflow fix for xlocale.h
RUN \
  ln -s /usr/include/locale.h /usr/include/xlocale.h

RUN \
  apt update -y && \
  apt install -y \
  bash \
  build-essential \
  cmake \
  cmake-curses-gui \
  curl \
  git \
  libffi-dev \
  libgdbm-dev \
  libncurses5-dev \
  libnss3-dev \
  libreadline-dev \
  libsqlite3-dev \
  libssl-dev \
  libbz2-dev \
  npm \
  python3 \
  python3-pip \
  python3-setuptools \
  zlib1g-dev

RUN \
  pip install \
  libclang \
  pyyaml \
  cerberus \
  argparse

WORKDIR /rapidjson/
RUN \
  git clone -b v1.1.0  --depth 1 https://github.com/Tencent/rapidjson.git .

WORKDIR /freetype/
RUN \
  git clone -b VER-2-13-3 --depth 1 https://github.com/freetype/freetype.git .

RUN \
  mkdir -p mkdir -p build-emcc && \
  cd build-emcc && \
  emcmake cmake -DWITH_ZLIB=OFF -DWITH_BZip2=OFF -DWITH_PNG=OFF -DCMAKE_EXE_LINKER_FLAGS="-fwasm-exceptions" .. && \
  emmake make -j8

ENV FREETYPE_INCLUDE_DIRS /freetype/build-emcc/include
ENV FREETYPE_LIBRARIES /freetype/build-emcc/libfreetype.a
ENV FREETYPE_LIBRARY /freetype/build-emcc/libfreetype.a

ENV OCCT_COMMIT_HASH_FULL 858dd02fed80c8ca68cf1d70a6e03ecf97f849ca
# ENV OCCT_COMMIT_HASH_FULL bb368e271e24f63078129283148ce83db6b9670a
WORKDIR /occt/
RUN \
  curl "https://git.dev.opencascade.org/gitweb/?p=occt.git;a=snapshot;h=${OCCT_COMMIT_HASH_FULL};sf=tgz" -o occt.tar.gz && \
  tar -xvf occt.tar.gz

RUN \
  export OCCT_COMMIT_HASH=$(echo ${OCCT_COMMIT_HASH_FULL} | cut -c 1-7) && \
  mv occt-$OCCT_COMMIT_HASH/* . && \
  mv occt-$OCCT_COMMIT_HASH/.[a-z0-9]* . || true && \
  rm occt-$OCCT_COMMIT_HASH -r

ARG threading=single-threaded
ENV threading=$threading


RUN \
  mkdir -p /opencascade.js/build/ /opencascade.js/dist/
  # && \
  # /opencascade.js/src/applyPatches.py

  FROM base-image AS base-occt-configured

WORKDIR /src/
RUN \
  emcmake cmake /occt/ \
  -DBUILD_LIBRARY_TYPE=static \
  -DCMAKE_BUILD_TYPE=debug \
  -D3RDPARTY_DIR=/ \
  -DBUILD_MODULE_Draw=false \
  -DBUILD_MODULE_Visualization=false \
  -DUSE_FREETYPE=false

RUN \
  emmake make -j8

RUN \
  ls -la

COPY src /opencascade.js/src

FROM base-occt-configured AS ready-to-bind

#RUN /opencascade.js/src/generateBindings.py ${threading}
#RUN /opencascade.js/src/compileBindings.py ${threading}

RUN python3 /opencascade.js/src/run.py
  
# RUN \
#   /opencascade.js/src/compileSources.py ${threading} && \
#   chmod -R 777 /opencascade.js/ && \
#   chmod -R 777 /occt

ENTRYPOINT ["/opencascade.js/src/buildFromYaml.py"]
