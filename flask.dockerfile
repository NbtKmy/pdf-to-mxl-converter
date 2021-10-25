# set base image (host OS)
FROM python:3.8

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# set the working directory in the container
ENV HOME=/code
RUN mkdir /code && \
    mkdir -p /code/src/mediafiles &&\
    mkdir /code/src/output

WORKDIR $HOME

# copy the dependencies file to the working directory
COPY ./requirements.txt $HOME

# install dependencies
RUN pip install -r requirements.txt

# Copy the codes and templates into the workdir
COPY ./src/ $HOME

# define the port
EXPOSE 8888