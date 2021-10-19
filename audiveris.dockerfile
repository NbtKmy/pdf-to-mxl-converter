FROM alpine as builder

RUN apk update && apk add openjdk11 \
        git \
        tesseract-ocr  \
		ttf-dejavu \
		tar
RUN git clone https://github.com/Audiveris/audiveris.git && \
        cd audiveris && \
        ./gradlew build && \
		mkdir /Audiveris && \
        tar -xvf /audiveris/build/distributions/Audiveris.tar -C /Audiveris  &&\
        mkdir /output &&\
        mkdir /input

FROM alpine 
COPY --from=builder /Audiveris/ /
RUN apk update && apk add openjdk11-jre \
        tesseract-ocr \
        tesseract-ocr-data-deu \
        tesseract-ocr-data-fra \
		font-bh-ttf \
		libuuid && \
		ln -s /usr/lib/libfontconfig.so.1 /usr/lib/libfontconfig.so && \
		ln -s /lib/libuuid.so.1 /usr/lib/libuuid.so.1 && \
		ln -s /lib/libc.musl-x86_64.so.1 /usr/lib/libc.musl-x86_64.so.1
ENV LD_LIBRARY_PATH /usr/lib

