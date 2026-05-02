# syntax=docker/dockerfile:1
FROM eclipse-temurin:25-jdk AS builder

ARG AUDIVERIS_REF=5.10.2

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch ${AUDIVERIS_REF} https://github.com/Audiveris/audiveris.git
WORKDIR /src/audiveris
RUN ./gradlew --no-daemon :app:distTar -x test
RUN mkdir /Audiveris && \
    tar -xf app/build/distributions/*.tar -C /Audiveris --strip-components=1

FROM eclipse-temurin:25-jre

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-fra \
        fontconfig \
        fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /Audiveris /Audiveris
RUN mkdir /input /output

CMD ["sleep", "infinity"]
