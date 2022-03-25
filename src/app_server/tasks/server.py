import logging, urllib
import threading
import time, sched
from concurrent import futures
from datetime import datetime
from typing import Dict
from urllib import error, request
from urllib.parse import parse_qs

import grpc
from google.api_core.exceptions import NotFound, FailedPrecondition, GoogleAPICallError
from .proto import (cloudtasks_pb2, cloudtasks_pb2_grpc,
                                         queue_pb2, task_pb2, target_pb2)
from google.protobuf import empty_pb2
from google.protobuf.timestamp_pb2 import Timestamp
from google.rpc.status_pb2 import Status

# Time to sleep between iterations of the threads
_LOOP_SLEEP_TIME = 0.1

DEFAULT_TARGET_PORT = 80
DEFAULT_TARGET_HOST = "localhost"

Queue = queue_pb2.Queue
Task = task_pb2.Task
Attempt = task_pb2.Attempt

logger = logging.getLogger("gcloud-tasks-emulator")


def _make_task_request(queue_name: str, task: Task, host: str, port: int):
    logger.info("[TASKS] Submitting task %s", task.name)

    headers = {}
    data = None

    if task.app_engine_http_request.relative_uri:
        method = target_pb2.HttpMethod.Name(task.app_engine_http_request.http_method)
        data = task.app_engine_http_request.body

        url = "http://%s:%d%s" % (
            host,
            port,
            task.app_engine_http_request.relative_uri
        )

        headers.update(getattr(task.app_engine_http_request, 'headers', {}))
        headers.update({
            'X-AppEngine-QueueName': queue_name,
            'X-AppEngine-TaskName': task.name.rsplit("/", 1)[-1],
            'X-AppEngine-TaskRetryCount': task.dispatch_count - 1,
            'X-AppEngine-TaskExecutionCount': task.dispatch_count - 1,
            'X-AppEngine-TaskETA': task.schedule_time.ToSeconds()
        })
    elif task.http_request.url:
        method = target_pb2.HttpMethod.Name(task.http_request.http_method)
        data = task.http_request.body
        url = task.http_request.url
        headers.update(getattr(task.http_request, "headers", {}))
        headers.update({
            'X-CloudTasks-QueueName': queue_name,
            'X-CloudTasks-TaskName': task.name.rsplit("/", 1)[-1],
            'X-CloudTasks-TaskRetryCount': task.dispatch_count - 1,
            'X-CloudTasks-TaskExecutionCount': task.dispatch_count - 1,
            'X-CloudTasks-TaskETA': task.schedule_time.ToSeconds()
        })
    else:
        raise Exception("Either app_engine_http_request or http_request is required")

    req = request.Request(url, method=method, data=data)
    for k, v in headers.items():
        req.add_header(k, v)

    logger.info("[TASKS] Requesting %s %s", req.method, req.full_url)
    return request.urlopen(req)


class QueueState(object):
    """
        Keeps the state of queues and tasks in memory
        so they can be processed
    """

    def __init__(self, target_host: str, target_port: int, max_retries: int):
        self._queues: Dict[str, Queue] = {}
        self._queue_tasks: Dict[str, Task] = {}
        self._target_host: str = target_host
        self._target_port: int = target_port
        self._max_retries: int = max_retries

    def create_queue(self, parent, queue: Queue):
        assert queue.name
        if queue.name not in self._queues:
            self._queues[queue.name] = queue
            self._queues[queue.name].state = (
                queue_pb2._QUEUE_STATE.values_by_name["RUNNING"].number
            )
            self._queue_tasks[queue.name] = []
            logger.info("[TASKS] Created queue %s", queue.name)

        return self._queues[queue.name]

    def update_queue(self, queue: Queue):
        if queue.name not in self._queues:
            self.create_queue(None, queue)
        else:
            # FIXME: Updating queue properties has
            # no effect currently on the emulator
            pass
        return self._queues[queue.name]

    def create_task(self, queue: Queue, task: Task):
        task.name = task.name or "%s/tasks/%s" % (
            queue, int(datetime.now().timestamp() * 1000000)
        )

        if task.app_engine_http_request.relative_uri:
            # Set a default http_method
            task.app_engine_http_request.http_method = (
                task.app_engine_http_request.http_method or target_pb2.HttpMethod.Value("POST")
            )
        elif task.http_request.url:
            # Set a default http_method
            task.http_request.http_method = (
                task.http_request.http_method or target_pb2.HttpMethod.Value("POST")
            )

        if queue not in self._queue_tasks:
            raise FailedPrecondition(f"Queue {queue} does not exist.")

        self._queue_tasks[queue].append(task)
        logger.info("[TASKS] Created task %s", task.name)
        return task

    def purge_queue(self, queue_name: str):
        if queue_name in self._queues:
            # Wipe the tasks out
            logger.info("[TASKS] Purging queue %s", queue_name)
            self._queue_tasks[queue_name] = []
            return self._queues[queue_name]
        else:
            logger.error(
                "[TASKS] Tried to purge an invalid queue: %s",
                queue_name
            )
            raise ValueError("Invalid queue: %s" % queue_name)

    def pause_queue(self, queue_name: str):
        if queue_name in self._queues:
            logger.info("[TASKS] Pausing queue %s", queue_name)
            self._queues[queue_name].state = (
                queue_pb2._QUEUE_STATE.values_by_name["PAUSED"].number
            )
            return self._queues[queue_name]
        else:
            logger.error(
                "[TASKS] Tried to pause an invalid queue: %s",
                queue_name
            )
            raise ValueError("Invalid queue: %s" % queue_name)

    def resume_queue(self, queue_name: str):
        if queue_name in self._queues:
            logger.info("[TASKS] Resuming queue %s", queue_name)
            self._queues[queue_name].state = (
                queue_pb2._QUEUE_STATE.values_by_name["RUNNING"].number
            )
            return self._queues[queue_name]
        else:
            logger.error(
                "[TASKS] Tried to resume an invalid queue: %s",
                queue_name
            )
            raise ValueError("Invalid queue: %s" % queue_name)

    def list_tasks(self, queue_name: str):
        return self._queue_tasks[queue_name]

    def queue_names(self):
        return list(self._queues)

    def queues(self):
        return list(self._queues.values())

    def queue(self, name: str) -> Queue:
        return self._queues[name]

    def delete_queue(self, name: str):
        if name in self._queues:
            logger.info("[TASKS] Deleting queue %s", name)
            del self._queues[name]

        if name in self._queue_tasks:
            del self._queue_tasks[name]

    def submit_task(self, task_name: str, force_run: bool = False) -> Task:
        """
            Actually executes a task. If force_run is True then the scheduled
            time will be ignored. This is used mainly for the RunTask API call.
        """

        try:
            queue_name = task_name.rsplit("/", 2)[0]
            if queue_name not in self._queue_tasks:
                raise ValueError("Not a valid queue")
        except IndexError:
            # Invalid task name, raise ValueError
            raise ValueError()

        # This is a special-case that does not exist
        # one the live server and it exists so that
        # local development servers can direct a task
        # to run on a particular port
        qs = task_name.rsplit("?", 1)[-1]
        if qs:
            params = parse_qs(qs)
            port = int(params.get("port", [self._target_port])[0])
            task_name = task_name.rsplit("?", 1)[0]
        else:
            port = self._target_port

        index = None

        # Locate the task in the queue
        for i, task in enumerate(self._queue_tasks[queue_name]):
            if task.name == task_name:
                index = i
                break
        else:
            logger.debug(
                "[TASKS] Tasks were: %s",
                [x.name for x in self._queue_tasks[queue_name]]
            )
            raise NotFound("Task not found: %s" % task_name)

        def now():
            current_time = Timestamp()
            current_time.GetCurrentTime()
            return current_time

        schedule_time = now()
        dispatch_time = None
        response_time = None
        response_status = 200

        task: Task = self._queue_tasks[queue_name].pop(index)  # Remove the task

        if (
            (not force_run) and
            task.HasField("schedule_time") and
            task.schedule_time.ToDatetime() >= schedule_time.ToDatetime()
        ):
            logger.info(
                "[TASKS] Task %s is scheduled for future execution. Moving it to the end of the queue.", task_name
            )
            self._queue_tasks[queue_name].append(task)
            return task

        task.dispatch_count += 1
        try:
            dispatch_time = now()
            response = _make_task_request(queue_name, task, self._target_host, port)
        except error.HTTPError as e:
            response_status = e.code
            logger.error("[TASKS] Error submitting task %s, reason: %s", task_name, e.reason)
        except (ConnectionError, error.URLError) as e:
            response_status = 500
            logger.error(
                "[TASKS] Error submitting task %s, reason: %s:\n%s: %s",
                task_name, getattr(e, 'reason', ''), type(e).__name__, e
            )
            logger.error(
                "[TASKS] Host was %s:%s" % (self._target_host, port)
            )
        except Exception as e:
            response_status = 500
            logger.error("[TASKS] Unexpected exception while submitting task %s.", task_name, e)
        else:
            response_status = response.status

        attempt = Attempt(
            schedule_time=schedule_time,
            dispatch_time=dispatch_time,
            response_time=response_time,
            response_status=Status(code=response_status)
        )

        kwargs = {
            "first_attempt": task.first_attempt or attempt,
            "last_attempt": attempt
        }

        task.MergeFrom(Task(**kwargs))

        assert task

        if 400 <= response_status < 600:
            if self._max_retries < 0 or task.dispatch_count <= self._max_retries:
                logger.info("[TASKS] Moving failed task %s to the back of the queue.", task_name)
                self._queue_tasks[queue_name].append(task)
            else:
                logger.info("[TASKS] Giving up failed task %s", task_name)

        return task


class Greeter(cloudtasks_pb2_grpc.CloudTasksServicer):
    def __init__(self, state: QueueState):
        super().__init__()

        self._state: QueueState = state

    def CreateQueue(self, request, context):
        try:
            return self._state.create_queue(request.parent, request.queue)
        except GoogleAPICallError as err:
            context.abort(err.grpc_status_code, err.message)

    def UpdateQueue(self, request, context):
        try:
            return self._state.update_queue(request.queue)
        except GoogleAPICallError as err:
            context.abort(err.grpc_status_code, err.message)

    def ListQueues(self, request, context):
        try:
            queues = [x for x in self._state.queues() if x.name.startswith(request.parent)]
            return cloudtasks_pb2.ListQueuesResponse(queues=queues)
        except GoogleAPICallError as err:
            context.abort(err.grpc_status_code, err.message)

    def GetQueue(self, request, context):
        try:
            return self._state.queue(request.name)
        except GoogleAPICallError as err:
            context.abort(err.grpc_status_code, err.message)

    def PauseQueue(self, request, context):
        try:
            return self._state.pause_queue(request.name)
        except GoogleAPICallError as err:
            context.abort(err.grpc_status_code, err.message)

    def ResumeQueue(self, request, context):
        try:
            return self._state.resume_queue(request.name)
        except GoogleAPICallError as err:
            context.abort(err.grpc_status_code, err.message)

    def PurgeQueue(self, request, context):
        try:
            return self._state.purge_queue(request.name)
        except GoogleAPICallError as err:
            context.abort(err.grpc_status_code, err.message)

    def ListTasks(self, request, context):
        try:
            return cloudtasks_pb2.ListTasksResponse(
                tasks=self._state.list_tasks(request.parent)
            )
        except GoogleAPICallError as err:
            context.abort(err.grpc_status_code, err.message)

    def DeleteQueue(self, request, context):
        try:
            self._state.delete_queue(request.name)
            return empty_pb2.Empty()
        except GoogleAPICallError as err:
            context.abort(err.grpc_status_code, err.message)

    def CreateTask(self, request, context):
        try:
            return self._state.create_task(request.parent, request.task)
        except GoogleAPICallError as err:
            context.abort(err.grpc_status_code, err.message)

    def RunTask(self, request, context):
        try:
            return self._state.submit_task(request.name, force_run=True)
        except GoogleAPICallError as err:
            context.abort(err.grpc_status_code, err.message)


class APIThread(threading.Thread):
    def __init__(self, state: QueueState, host: str, port: int, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._state = state
        self._port = port
        self._host = host
        self._is_running = threading.Event()
        self._httpd = None

    def run(self):
        self._httpd = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        cloudtasks_pb2_grpc.add_CloudTasksServicer_to_server(
            Greeter(self._state), self._httpd
        )

        interface = '%s:%s' % (self._host,self._port)
        self._httpd.add_insecure_port(interface)

        logger.info("[TASKS] Starting API server at %s", interface)
        self._httpd.start()

        while self._is_running.is_set():
            time.sleep(_LOOP_SLEEP_TIME)

    def join(self, timeout=None):
        self._is_running.clear()
        if self._httpd:
            self._httpd.stop(grace=0)
        logger.info("[TASKS] Stopping API server")


class Processor(threading.Thread):
    def __init__(self, state: QueueState):
        super().__init__()

        self._state: QueueState = state
        self._is_running = threading.Event()
        self._known_queues = set()
        self._queue_threads = {}

    def run(self):
        self._is_running.set()
        logger.info("[TASKS] Starting task processor")
        while self._is_running.is_set():
            queue_names = self._state.queue_names()
            for queue in queue_names:
                self.process_queue(queue)
            time.sleep(_LOOP_SLEEP_TIME)

    def _process_queue(self, queue: str):
        while self._is_running.is_set():
            # Queue was deleted, stop processing
            if queue not in self._state._queues:
                break

            if queue not in self._state._queue_tasks:
                break

            if self._state.queue(queue).state == queue_pb2._QUEUE_STATE.values_by_name["RUNNING"].number:
                tasks = self._state._queue_tasks[queue][:]

                while tasks:
                    task = tasks.pop(0)
                    logger.info("[TASKS] Processing next task %s", task.name)
                    self._state.submit_task(task.name)

            time.sleep(_LOOP_SLEEP_TIME)

    def process_queue(self, queue_name: str):
        if queue_name not in self._known_queues:
            # A queue was just created
            self._known_queues.add(queue_name)

            thread = threading.Thread(
                target=self._process_queue, args=[queue_name]
            )
            self._queue_threads[queue_name] = thread
            self._queue_threads[queue_name].start()

    def join(self, timeout=None):
        self._is_running.clear()
        for thread in self._queue_threads.values():
            if thread.is_alive():
                thread.join(timeout=0)

        super().join(timeout)


class CronScheduler(threading.Thread):
    def __init__(self, crons, host, port):
        super().__init__()
        self._crons = crons
        self._host = f"http://{host}:{port}"
        self._scheduler = sched.scheduler(time.time, time.sleep)
        self._event = None

    def get_intervall_in_seconds(self,schedule_string):
        mult_map = {
            "seconds":1,
            "minutes":60,
            "mins":60,
            "hours": 60*60,
            "days": 60*60*24
        }


        schedule_parts = self._crons[0]["schedule"].split(" ")
        multiplier =  mult_map[schedule_parts[2]]
        return int(schedule_parts[1])*multiplier


    def run(self):
        logger.info("[TASKS] Starting cron processor")
        intervall = self.get_intervall_in_seconds(self._crons[0]["schedule"])
        url = self._host+self._crons[0]["url"]

        def call_cron_hook(sc):
            print("Doing stuff...")
            print(url)
            urllib.request.urlopen(url)
            sc.enter(intervall, 1, call_cron_hook, (sc,))
        self._event = self._scheduler.enter(intervall, 1, call_cron_hook, (self._scheduler,))
        self._scheduler.run()

    def join(self, timeout=None):
        for entry in self._scheduler.queue:
            self._scheduler.cancel(entry)
        super().join(timeout)

class Server(object):
    def __init__(self, host, port, target_host, target_port, default_queue_names, max_retries, crons):
        self._state = QueueState(target_host, target_port, max_retries)
        self._api = APIThread(self._state, host, port)
        self._processor = Processor(self._state)
        self._cron_processor = CronScheduler(crons,host,target_port)
        self._crons = crons

        for default_queue_name in default_queue_names:
            parent = default_queue_name.rsplit("/", 3)[0]
            self._state.create_queue(
                parent, Queue(name=default_queue_name)
            )

    def start(self):
        self._api.start()  # Start the API thread
        self._processor.start()
        if self._crons:
            self._cron_processor.start()

    def stop(self):
        self._processor.join(timeout=1)
        self._api.join(timeout=1)
        if self._crons:
            self._cron_processor.join(timeout=1)

    def run(self):
        try:
            self.start()

            logger.info("[TASKS] All services started")

            while True:
                try:
                    time.sleep(_LOOP_SLEEP_TIME)
                except KeyboardInterrupt:
                    break

        finally:
            self.stop()




def create_server(
    host, port,
    target_host=DEFAULT_TARGET_HOST, target_port=DEFAULT_TARGET_PORT,
    default_queue_names=None, max_retries=-1, crons=None
):
    return Server(host, port, target_host, target_port, default_queue_names or [], max_retries,crons)
