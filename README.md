## App Server
The App Server project allows to start a gunicorn server to deliver application logic. Which is wrapped by a [werkzeug](https://werkzeug.palletsprojects.com/) server to deliver staticfiles. 
Primarily the App Server is used as a lightweight alternative to the [dev_appserver](https://cloud.google.com/appengine/docs/standard/python3/testing-and-deploying-your-app?hl=de#local-dev-server) for the google appengine build with python. 
But all [gunicorn](https://gunicorn.org/) projects can be started with it. 
The static paths are stored in an app.yaml as handlers and are therefore compatible to google appengine projects.

## Getting Started
Take a look at the example folder. Here you can find a start.sh which starts the App Server on port 8080.

### External APIs
App Server allows app code to connect to external APIs, eg [Google Cloud Datastore](https://cloud.google.com/datastore/docs/), like normal. To use the [local datastore emulator](https://cloud.google.com/datastore/docs/tools/datastore-emulator), first start it, then in a separate shell [set the appropriate environment variables](https://cloud.google.com/datastore/docs/tools/datastore-emulator#setting_environment_variables) (notably `DATASTORE_EMULATOR_HOST` and `DATASTORE_DATASET`) to point your app to it before you run App Server.

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
