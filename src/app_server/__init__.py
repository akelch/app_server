import sys, os, re, subprocess, yaml, argparse, time, mimetypes, logging
import typing as t
from werkzeug.wrappers import Request, Response
from werkzeug.middleware.shared_data import SharedDataMiddleware
from werkzeug.middleware.http_proxy import ProxyMiddleware
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.serving import run_simple, WSGIRequestHandler,_ansi_style,_log_add_style
from werkzeug.wsgi import get_path_info, wrap_file
from werkzeug.utils import get_content_type
from werkzeug.http import http_date, is_resource_modified
from werkzeug._internal import _logger
from werkzeug.urls import uri_to_iri, url_unquote



__version__ = "0.9.4"

subprocesses = []

class myWSGIRequestHandler(WSGIRequestHandler):
    def log_date_time_string(self):
        """Return the current time formatted for logging."""
        now = time.time()
        year, month, day, hh, mm, ss, x, y, z = time.localtime(now)
        s = "%04d-%02d-%02d %02d:%02d:%02d" % (
            year , month,day , hh, mm, ss)
        return s

    def log_request(self, code: t.Union[int, str] = "-", size: t.Union[int, str] = "-") -> None:

        """coloring the status code"""
        try:
            path = uri_to_iri(self.path)
            msg = f"[{self.command}] {path}"
        except AttributeError:
            # path isn't set if the requestline was bad
            msg = self.requestline

        code = str(code)

        log_type = "info"
        if code != "200": #possibility to filter 200 requests
            log_type = "warning"

        if _log_add_style:
            if code[0] == "1":  # 1xx - Informational
                code = _ansi_style(code, "bold")
            elif code == "200":  # 2xx - Success
                pass
            elif code == "304":  # 304 - Resource Not Modified
                code = _ansi_style(code, "cyan")
            elif code[0] == "3":  # 3xx - Redirection
                code = _ansi_style(code, "green")
            elif code == "404":  # 404 - Resource Not Found
                code = _ansi_style(code, "yellow")
            elif code[0] == "4":  # 4xx - Client Error
                code = _ansi_style(code, "bold", "red")
            else:  # 5xx, or any other response
                code = _ansi_style(code, "bold", "red")

        self.log(log_type, '[%s] %s', code, msg)

    def log(self, type: str, message: str, *args) -> None:
        global _logger

        if _logger is None:
            _logger = logging.getLogger("werkzeug")
            _logger.setLevel(logging.INFO)
            _logger.addHandler(logging.StreamHandler())

        getattr(_logger, type)(f"[{self.log_date_time_string()}] {message % args}")


class WrappingApp(object):
    """simple wrapping app"""

    def __init__(self, config):
        pass

    def wsgi_app(self, environ, start_response):
        request = Request(environ)
        response = Response(f'Path not found or invalid: {request.path}', status=404)
        return response(environ, start_response)

    def __call__(self, environ, start_response):
        return self.wsgi_app(environ, start_response)

class myProxy(ProxyMiddleware):
    """this addition allows to redirect all routes to given targets"""

    def __init__(self, app, targets, chunk_size=2 << 13, timeout=10):
        super().__init__(app, targets, chunk_size, timeout)

        def _set_defaults(opts):
            opts.setdefault("remove_prefix", False)
            opts.setdefault("host", "<auto>")
            opts.setdefault("headers", {})
            opts.setdefault("ssl_context", None)
            return opts

        self.targets = {
            f"{k}": _set_defaults(v) for k, v in targets.items()
        }

    def __call__(self, environ: "WSGIEnvironment", start_response: "StartResponse") -> t.Iterable[bytes]:
        path = get_path_info(environ, charset='utf-8', errors='replace')
        app = self.app
        for prefix, opts in self.targets.items():
            if path.startswith(prefix):
                app = self.proxy_to(opts, path, prefix)
                break

        return app(environ, start_response)

class myDispatcher(DispatcherMiddleware):
    """use regex to find a matching route"""

    def __call__(self, environ, start_response):
        app = self.mounts["/"]

        for route, _app in self.mounts.items():
            if re.match(route, environ["PATH_INFO"]):
                app = _app
                break
        return app(environ, start_response)


class mySharedData(SharedDataMiddleware):
    """use regex to find a matching files"""

    def __init__(
            self,
            app,
            exports,
            disallow: None = None,
            cache: bool = True,
            cache_timeout: int = 60 * 60 * 12,
            fallback_mimetype: str = "application/octet-stream",
    ) -> None:
        self.org_exports = exports.copy()
        super().__init__(app, exports, disallow, cache, cache_timeout, fallback_mimetype)

    def __call__(self, environ, start_response):
        path = get_path_info(environ)
        file_loader = None

        for search_path, loader in self.exports:
            # lets check for regex, and inject real_path
            if re.match(search_path, path):
                real_path = re.sub(search_path, self.org_exports[search_path], path, 1)
                real_filename, file_loader = self.get_file_loader(real_path)(None)

                if file_loader is not None:
                    break

            if search_path == path:
                real_filename, file_loader = loader(None)

                if file_loader is not None:
                    break

            if not search_path.endswith("/"):
                search_path += "/"

            if path.startswith(search_path):
                real_filename, file_loader = loader(path[len(search_path):])

                if file_loader is not None:
                    break

        if file_loader is None or not self.is_allowed(real_filename):  # type: ignore
            return self.app(environ, start_response)

        guessed_type = mimetypes.guess_type(real_filename)  # type: ignore
        mime_type = get_content_type(guessed_type[0] or self.fallback_mimetype, "utf-8")

        try:
            f, mtime, file_size = file_loader()
        except:
            return self.app(environ, start_response)  # 404

        headers = [("Date", http_date())]

        if self.cache:
            timeout = self.cache_timeout
            etag = self.generate_etag(mtime, file_size, real_filename)  # type: ignore
            headers += [
                ("Etag", f'"{etag}"'),
                ("Cache-Control", f"max-age={timeout}, public"),
            ]

            if not is_resource_modified(environ, etag, last_modified=mtime):
                f.close()
                start_response("304 Not Modified", headers)
                return []

            headers.append(("Expires", http_date(time.time() + timeout)))
        else:
            headers.append(("Cache-Control", "public"))

        headers.extend(
            (
                ("Content-Type", mime_type),
                ("Content-Length", str(file_size)),
                ("Last-Modified", http_date(mtime)),
            )
        )
        start_response("200 OK", headers)
        return wrap_file(environ, f)


def start_server(host, port, gunicorn_port, appFolder, appYaml, timeout, protocol="http"):
    """use the dispatcherMiddleware to connect SharedDataMiddleware and ProxyMiddleware with the wrapping app."""
    app = WrappingApp({})
    apps = {}

    # make shared middlewares for static files as configured in app.yaml
    for route in appYaml["handlers"]:
        if path := route.get("static_dir"):
            pattern = route["url"] + "/.*"

        elif path := route.get("static_files"):
            pattern = route["url"]

        else:
            continue  # skip

        # print(pattern, route["url"], path)
        apps[pattern] = mySharedData(app.wsgi_app, {route["url"]: os.path.join(appFolder, path)})

    apps.update({"/": myProxy(app.wsgi_app, {
        "/": {
            "target": f"{protocol}://{host}:{gunicorn_port}/",
            "host": f"{host}:{port}"
        }
    },timeout=timeout)})
    app.wsgi_app = myDispatcher(app.wsgi_app, apps)

    run_simple(host, port, app, use_debugger=False, use_reloader=True, threaded=True, request_handler=myWSGIRequestHandler)


def envVars(application_id, args):
    """set nessesary environment variables"""
    os.environ["GAE_ENV"] = "localdev"
    os.environ["CLOUDSDK_CORE_PROJECT"] = application_id
    os.environ["GOOGLE_CLOUD_PROJECT"] = application_id
    os.environ["GAE_VERSION"] = str(time.time())
    os.environ["GRPC_ENABLE_FORK_SUPPORT"] = "0"

    if args.storage:
        os.environ["STORAGE_EMULATOR_HOST"] = f"http://{args.host}:{args.storage_port}"

    if args.tasks:
        os.environ["TASKS_EMULATOR"] = f"{args.host}:{args.tasks_port}"

def start_gunicorn(args, appYaml, appFolder, myFolder):
    # Gunicorn call command
    entrypoint = appYaml.get("entrypoint", "gunicorn -b :$PORT -w $WORKER --threads $THREADS "
                                           "--disable-redirect-access-to-syslog main:app")
    for var, value in {
        "PORT": args.gunicorn_port,
        "WORKER": args.worker,
        "THREADS": args.threads
    }.items():
        entrypoint = entrypoint.replace(f"${var}", str(value))

    entrypoint = entrypoint.split()
    if "--reload" not in entrypoint:
        entrypoint.insert(1, "--reload")
    if "--reuse-port" not in entrypoint:
        entrypoint.insert(1, "--reuse-port")

    os.chdir(appFolder)
    subprocesses.append(subprocess.Popen(entrypoint))
    os.chdir(myFolder)

def main():
    """main entrypoint

    collect parameters
    set environment variables
    start gunicorn
    start wrapping app
    """
    ap = argparse.ArgumentParser(
        description="alternative dev_appserver"
    )

    ap.add_argument("config_paths", metavar='yaml_path', nargs='+', help='Path to app.yaml file')
    ap.add_argument(
        '-A', '--application', action='store', dest='app_id', required=True, help='Set the application id')
    ap.add_argument('--host', default="localhost", help='host name to which application modules should bind')
    ap.add_argument('--port', type=int, default=8080, help='port to which we bind the application')
    ap.add_argument('--gunicorn_port', type=int, default=8090, help='internal gunicorn port')
    ap.add_argument('--worker', type=int, default=1, help='amount of gunicorn workers')
    ap.add_argument('--threads', type=int, default=5, help='amount of gunicorn threads')
    ap.add_argument('--timeout', type=int, default=60, help='Time is seconds befor gunicorn abort a rquest')
    ap.add_argument('-V', '--version', action='version', version='%(prog)s ' + __version__)

    ap.add_argument('--storage', default=False, action="store_true", dest="storage", help="also start Storage Emulator")
    ap.add_argument('--storage_port', type=int, default=8092, help='internal Storage Emulator Port')

    ap.add_argument('--tasks', default=False, action='store_true', dest="tasks", help='also start Task-Queue Emulator')
    ap.add_argument('--tasks_port', type=int, default=8091, help='internal Task-Queue Emulator Port')

    ap.add_argument('--cron', default=False, action='store_true', dest="cron", help='also start Cron Emulator')

    args = ap.parse_args()
    envVars(args.app_id, args)

    myFolder = os.getcwd()
    appFolder = os.path.abspath(args.config_paths[0])

    # load & parse the app.yaml
    with open(os.path.join(appFolder, "app.yaml"), "r") as f:
        appYaml = yaml.load(f, Loader=yaml.Loader)

    # Check for correct runtime
    myRuntime = f"python{sys.version_info.major}{sys.version_info.minor}"
    appRuntime = appYaml["runtime"]
    assert appRuntime == myRuntime, f"app.yaml specifies {appRuntime} but you're on {myRuntime}, please correct this."

    if "WERKZEUG_RUN_MAIN" in os.environ and os.environ["WERKZEUG_RUN_MAIN"]:
        #only start subprocesses wenn reloader starts

        if args.storage:
            storage_subprocess =subprocess.Popen(
                f"gcloud-storage-emulator start --port={args.storage_port} --default-bucket={args.app_id}.appspot.com".split())

            subprocesses.append(storage_subprocess)


        if args.tasks and os.path.exists(os.path.join(appFolder, 'queue.yaml')):
            cron = ""
            if args.cron:
                cron = f"--cron-yaml={os.path.join(appFolder, 'cron.yaml')}"

            tasks_subprocess = subprocess.Popen(
                f"gcloud-tasks-emulator start -p={args.tasks_port} -t={args.port} {cron} --queue-yaml={os.path.join(appFolder, 'queue.yaml')} --queue-yaml-project={args.app_id} --queue-yaml-location=local -r 50".split())

            subprocesses.append(tasks_subprocess)

        start_gunicorn(args, appYaml, appFolder, myFolder)

    start_server(args.host, args.port, args.gunicorn_port, appFolder, appYaml, args.timeout)

    try:
        for process in subprocesses:
            process.kill()
    except:
        pass


if __name__ == '__main__':
    main()

