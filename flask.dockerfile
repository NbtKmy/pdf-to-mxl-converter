# set base image (host OS)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# build deps for verovio source build (no arm64 wheel as of 5.1.0)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        swig \
    && rm -rf /var/lib/apt/lists/*

# set the working directory in the container
ENV HOME=/code
RUN mkdir /code && \
    mkdir -p /code/src/mediafiles && \
    mkdir /code/src/output

WORKDIR $HOME

# copy the dependencies file to the working directory
COPY ./requirements.txt $HOME

# install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the codes and templates into the workdir
COPY ./src/ $HOME

# define the port
EXPOSE 8888
