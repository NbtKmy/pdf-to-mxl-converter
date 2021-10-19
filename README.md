# image-to-mxl-converter
This app converts a pdf file (music note image) into a .mxl-file with [Audiveris (stable version)] (https://github.com/Audiveris/audiveris). I've used the Docker image [toprock/audiveris] (https://hub.docker.com/r/toprock/audiveris') (slightly changed). This conversion process takes several minutes (and i recommend to use only a very small pdf file). So keep calm & be patient...


## Requirements

You need:

[Docker](https://docs.docker.com/)

[Docker-compose](https://docs.docker.com/compose/)

## Usage
Clone this repo:

```
git clone https://github.com/NbtKmy/pdf-to-mxl-converter.git
```

And go into the directory which you just cloned. 
And there put the command:

```
docker-compose up --build -d
```

Then 2 containers (flask and audiveris) are build and started.

You find the web app in the browser under 'localhost:8888'

## Flask configuration
[Flask](https://flask.palletsprojects.com/en/2.0.x/) is used for this web app. 

In the default configuration you find this app in the debug mode. 

Before you'll go further into the production mode, you may consider;
1. Create your secret key for the flask server
1. Change the port number


