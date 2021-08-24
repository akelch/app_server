## App Server
The App Server project allows to start a gunicorn server to deliver application logic. Which is wrapped by a [werkzeug](https://werkzeug.palletsprojects.com/) server to deliver staticfiles. 
Primarily the App Server is used as a lightweight alternative to the [dev_appserver](https://cloud.google.com/appengine/docs/standard/python3/testing-and-deploying-your-app?hl=de#local-dev-server) for the google appengine build with python. 
But all [gunicorn](https://gunicorn.org/) projects can be started with it. 
The static paths are stored in an app.yaml as handlers and are therefore compatible to google appengine projects.

## Getting Started
Take a look at the example folder. Here you can find a start.sh which starts the App Server on port 8081.

### Dependencies
The app server dependents on the following packages
* [werkzeug](https://werkzeug.palletsprojects.com/)
* [pyyaml](https://pyyaml.org/wiki/PyYAMLDocumentation)
* [gunicorn](https://gunicorn.org/)

### Installation
use pip or pipenv to install this package
 ```sh
    pip install app-server
   ```

## License

Distributed under the MIT License. See `LICENSE` for more information.
