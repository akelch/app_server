import argparse
import logging
import mimetypes
import os
import re
import subprocess
import sys
import time
import typing as t

import yaml
from werkzeug._internal import _logger  # noqa
from werkzeug.http import http_date, is_resource_modified
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.http_proxy import ProxyMiddleware
from werkzeug.middleware.shared_data import SharedDataMiddleware
from werkzeug.serving import run_simple, WSGIRequestHandler, _ansi_style, \
    _log_add_style
from werkzeug.urls import uri_to_iri
from werkzeug.utils import get_content_type
from werkzeug.wrappers import Request, Response
from werkzeug.wsgi import get_path_info, wrap_file

__version__ = "0.9.10"

subprocesses = []


class MainWSGIRequestHandler(WSGIRequestHandler):
    def log_date_time_string(self):
        """Return the current time formatted for logging."""
        now = time.time()
        year, month, day, hh, mm, ss, x, y, z = time.localtime(now)
        s = "%04d-%02d-%02d %02d:%02d:%02d" % (
            year, month, day, hh, mm, ss)
        return s

    def log_request(
        self,
        code: t.Union[int, str] = "-",
        size: t.Union[int, str] = "-",
    ) -> None:
        """coloring the status code"""
        try:
            path = uri_to_iri(self.path)
            msg = f"[{self.command}] {path}"
        except AttributeError:
            # path isn't set if the requestline was bad
            msg = self.requestline

        code = str(code)

        log_type = "info"
        if code != "200":  # possibility to filter 200 requests
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

        getattr(_logger, type)(
            f"[{self.log_date_time_string()}] {message % args}")


class WrappingApp:
    """simple wrapping app"""

    def __init__(self, config):
        pass

    def wsgi_app(self, environ, start_response):
        request = Request(environ)
        response = Response(f'Path not found or invalid: {request.path}',
                            status=404)
        return response(environ, start_response)

    def __call__(self, environ, start_response):
        return self.wsgi_app(environ, start_response)


class Proxy(ProxyMiddleware):
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

    def __call__(self, environ: "WSGIEnvironment",
                 start_response: "StartResponse") -> t.Iterable[bytes]:
        path = get_path_info(environ, charset='utf-8', errors='replace')
        app = self.app
        for prefix, opts in self.targets.items():
            if path.startswith(prefix):
                app = self.proxy_to(opts, path, prefix)
                break

        return app(environ, start_response)


class Dispatcher(DispatcherMiddleware):
    """use regex to find a matching route"""

    def __call__(self, environ, start_response):
        app = self.mounts["/"]

        for route, _app in self.mounts.items():
            if re.match(route, environ["PATH_INFO"]):
                app = _app
                break
        return app(environ, start_response)


class SharedData(SharedDataMiddleware):
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
        super().__init__(app, exports, disallow, cache, cache_timeout,
                         fallback_mimetype)

    def __call__(self, environ, start_response):
        path = get_path_info(environ)
        file_loader = None

        for search_path, loader in self.exports:
            # lets check for regex, and inject real_path
            if re.match(search_path, path):
                real_path = re.sub(search_path, self.org_exports[search_path],
                                   path, 1)
                real_filename, file_loader = self.get_file_loader(real_path)(
                    None)

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

        if file_loader is None or not self.is_allowed(real_filename):  # noqa
            return self.app(environ, start_response)

        guessed_type = mimetypes.guess_type(real_filename)  # type: ignore
        mime_type = get_content_type(guessed_type[0] or self.fallback_mimetype,
                                     "utf-8")

        try:
            f, mtime, file_size = file_loader()
        except:
            return self.app(environ, start_response)  # 404

        headers = [("Date", http_date())]

        if self.cache:
            timeout = self.cache_timeout
            etag = self.generate_etag(mtime, file_size,
                                      real_filename)  # type: ignore
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


def start_server(
    host: str,
    port: int,
    gunicorn_port: int,
    app_folder: str,
    app_yaml: dict,
    timeout: int,
    protocol: str = "http",
) -> None:
    """use the dispatcherMiddleware to connect SharedDataMiddleware and ProxyMiddleware with the wrapping app."""
    app = WrappingApp({})
    apps = {}

    # make shared middlewares for static files as configured in app.yaml
    for route in app_yaml["handlers"]:
        if path := route.get("static_dir"):
            pattern = route["url"] + "/.*"

        elif path := route.get("static_files"):
            pattern = route["url"]

        else:
            continue  # skip

        # print(pattern, route["url"], path)
        apps[pattern] = SharedData(
            app.wsgi_app, {route["url"]: os.path.join(app_folder, path)}
        )

    apps["/"] = Proxy(app.wsgi_app, {
        "/": {
            "target": f"{protocol}://{host}:{gunicorn_port}/",
            "host": None
        }
    }, timeout=timeout)
    app.wsgi_app = Dispatcher(app.wsgi_app, apps)

    run_simple(host, port, app, use_debugger=False, use_reloader=True,
               threaded=True, request_handler=MainWSGIRequestHandler)


def set_env_vars(application_id: str, args: argparse.Namespace, app_yaml: dict):
    """set necessary environment variables"""
    # First, merge the app.yaml into the environment so that the variables
    # from the CLI can overwrite it.
    if env_vars := app_yaml.get("env_variables"):
        if not isinstance(env_vars, dict):
            raise TypeError(
                f"env_variables section in app.yaml must be a dict. Got {type(env_vars)}")
        os.environ |= {k: str(v) for k, v in app_yaml["env_variables"].items()}

    os.environ["GAE_ENV"] = "localdev"
    os.environ["CLOUDSDK_CORE_PROJECT"] = application_id
    os.environ["GOOGLE_CLOUD_PROJECT"] = application_id
    os.environ["GAE_VERSION"] = str(time.time())
    os.environ["GRPC_ENABLE_FORK_SUPPORT"] = "0"

    if args.storage:
        os.environ["STORAGE_EMULATOR_HOST"] = \
            f"http://{args.host}:{args.storage_port}"

    if args.tasks:
        os.environ["TASKS_EMULATOR"] = f"{args.host}:{args.tasks_port}"

    # Merge environment variables from CLI parameter
    if args.env_var:
        os.environ |= dict(v.split("=", 1) for v in args.env_var)


def patch_gunicorn():
    import gunicorn.workers.base
    with open(gunicorn.workers.base.__file__, 'r+') as file:
        content = file.read()

        if "except (SyntaxError, NameError) as e:" in content:
            return 0

        file.seek(0)
        file.write(content.replace(
            '        except SyntaxError as e:',
            '        except (SyntaxError, NameError) as e:'
        ))


def start_gunicorn(
    args: argparse.Namespace,
    app_yaml: dict,
    app_folder: str,
) -> None:
    # Gunicorn call command
    if not (entrypoint := args.entrypoint):
        entrypoint = app_yaml.get(
            "entrypoint",
            "gunicorn -b :$PORT --disable-redirect-access-to-syslog main:app"
        )
    entrypoint = entrypoint.replace(f"$PORT", str(args.gunicorn_port))
    # Remove -w / --workers / --threads arguments,
    # we set them later with the values from our argparser
    entrypoint = re.sub(r"\s+-(w|-workers|-threads)\s+\d+", " ", entrypoint)

    entrypoint = entrypoint.split()
    entrypoint.extend(["--workers", str(args.workers)])
    entrypoint.extend(["--threads", str(args.threads)])

    if "--reload" not in entrypoint:
        entrypoint.insert(1, "--reload")
    if "--reuse-port" not in entrypoint:
        entrypoint.insert(1, "--reuse-port")

    entrypoint.extend(["--timeout", str(args.timeout)])

    subprocesses.append(subprocess.Popen(entrypoint, cwd=app_folder))


def main():
    """main entrypoint

    collect parameters
    set environment variables
    start gunicorn
    start wrapping app
    """
    ap = argparse.ArgumentParser(
        description="alternative dev_appserver",
        epilog=f"Version: {__version__}"
    )

    ap.add_argument("config_paths", metavar='yaml_path', nargs='+',
                    help='Path to app.yaml file')
    ap.add_argument(
        '-A', '--application', action='store', dest='app_id', required=True,
        help='Set the application id')
    ap.add_argument('--host', default="localhost",
                    help='host name to which application modules should bind')
    ap.add_argument('--entrypoint', type=str, default=None,
                    help='The entrypoint is the basic gunicorn command. By default, it\'s taken from app.yaml. '
                         'This parameter can be used to set a different entrypoint. '
                         'To provide this parameter via ViUR-CLI, you have to double quote it: '
                         ' --entrypoint "\'gunicorn -b :\$PORT --disable-redirect-access-to-syslog main:app\'"')
    ap.add_argument('--port', type=int, default=8080,
                    help='port to which we bind the application')
    ap.add_argument('--gunicorn_port', type=int, default=8090,
                    help='internal gunicorn port')
    ap.add_argument('--workers', '--worker', type=int, default=1,
                    help='amount of gunicorn workers')
    ap.add_argument('--threads', type=int, default=5,
                    help='amount of gunicorn threads')
    ap.add_argument('--timeout', type=int, default=60,
                    help='Time is seconds before gunicorn abort a request')
    ap.add_argument('-V', '--version', action='version',
                    version='%(prog)s ' + __version__)

    ap.add_argument('--storage', default=False, action="store_true",
                    dest="storage", help="also start Storage Emulator")
    ap.add_argument('--storage_port', type=int, default=8092,
                    help='internal Storage Emulator Port')

    ap.add_argument('--tasks', default=False, action='store_true', dest="tasks",
                    help='also start Task-Queue Emulator')
    ap.add_argument('--tasks_port', type=int, default=8091,
                    help='internal Task-Queue Emulator Port')

    ap.add_argument('--cron', default=False, action='store_true', dest="cron",
                    help='also start Cron Emulator')

    ap.add_argument(
        '--env_var', metavar="KEY=VALUE", nargs="*",
        help="Set environment variable for the runtime. Each env_var is in "
             "the format of KEY=VALUE, and you can define multiple "
             "environment variables. You can also define them in app.yaml."
    )

    args = ap.parse_args()

    app_folder = os.path.abspath(args.config_paths[0])

    # load & parse the app.yaml
    with open(os.path.join(app_folder, "app.yaml"), "r") as f:
        app_yaml = yaml.load(f, Loader=yaml.Loader)

    set_env_vars(args.app_id, args, app_yaml)
    patch_gunicorn()

    # Check for correct runtime
    current_runtime = f"python{sys.version_info.major}{sys.version_info.minor}"
    app_runtime = app_yaml["runtime"]
    assert app_runtime == current_runtime, f"app.yaml specifies {app_runtime} but you're on {current_runtime}, please correct this."

    if "WERKZEUG_RUN_MAIN" in os.environ and os.environ["WERKZEUG_RUN_MAIN"]:
        # only start subprocesses wenn reloader starts

        if args.storage:
            storage_subprocess = subprocess.Popen(
                f"gcloud-storage-emulator start --port={args.storage_port}"
                f" --default-bucket={args.app_id}.appspot.com".split())

            subprocesses.append(storage_subprocess)

        if args.tasks and os.path.exists(
            os.path.join(app_folder, 'queue.yaml')):
            cron = ""
            if args.cron:
                cron = f"--cron-yaml={os.path.join(app_folder, 'cron.yaml')}"

            tasks_subprocess = subprocess.Popen(
                f"gcloud-tasks-emulator start -p={args.tasks_port} -t={args.port} {cron}"
                f" --queue-yaml={os.path.join(app_folder, 'queue.yaml')}"
                f" --queue-yaml-project={args.app_id} --queue-yaml-location=local -r 50".split())

            subprocesses.append(tasks_subprocess)

        start_gunicorn(args, app_yaml, app_folder)

    start_server(args.host, args.port, args.gunicorn_port, app_folder, app_yaml,
                 args.timeout)

    try:
        for process in subprocesses:
            process.kill()
    except:
        pass


if __name__ == '__main__':
    main()
