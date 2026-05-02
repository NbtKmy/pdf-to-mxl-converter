# pdf-to-mxl-converter
This app converts a music score PDF into a `.mxl` file with [Audiveris](https://github.com/Audiveris/audiveris). The Audiveris container is built directly from the upstream sources, currently pinned to **Audiveris 5.10.2** (which requires Java 25 — installed inside the container, no host setup needed). The conversion process may take several minutes (and it is recommended to use only a very small file). So keep calm & be patient...

> Note: the very first `docker compose up` builds Audiveris from source, which downloads Gradle and all dependencies. Expect the initial build to take a while; subsequent runs reuse the cached image.


## Requirements

You need:

[Docker](https://docs.docker.com/) (with Compose v2 — `docker compose`)

## Usage
Clone this repo:

```
git clone https://github.com/NbtKmy/pdf-to-mxl-converter.git
```

And go into the directory which you just cloned. 
And there put the command:

```
docker compose up -d
```

Then 2 containers (`flask` and `audiveris`) are built and started.

You find the web app in the browser under `http://localhost:8888`.

To upgrade Audiveris to a different release, override the build arg:

```
docker compose build --build-arg AUDIVERIS_REF=<tag> audiveris
```

## Flask configuration
[Flask](https://flask.palletsprojects.com/en/2.0.x/) is used for this web app. 

In the default configuration you find this app in the debug mode. 

Before you'll go further into the production mode, you may consider;
1. Create your secret key for the flask server
1. Change the port number


